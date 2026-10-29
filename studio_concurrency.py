"""Bounded Studio text work with one durable-result writer per batch.

The application runs one server worker. Request slots are shared by all Studio
runs for the same owner and provider origin, including different model names.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Event, Lock
import time
from urllib.parse import urlsplit


TEXT_CONCURRENCY = 3
_gate_lock = Lock()
_gates = {}
_batch_stats = ContextVar('studio_text_batch_stats', default=None)


@dataclass
class _Gate:
    semaphore: BoundedSemaphore = field(default_factory=lambda: BoundedSemaphore(TEXT_CONCURRENCY))
    users: int = 0


def _scope(owner_id, model):
    # Do not key by model ID: several models often share the same quota.
    # The key contains no credentials and is never exposed in user progress.
    url = urlsplit(str(model.get('base_url') or ''))
    provider = (url.hostname or '').lower()
    if provider and url.port and url.port not in (80, 443):
        provider += ':' + str(url.port)
    provider = provider or str(model.get('provider_id') or 'default')
    return str(owner_id), provider


@contextmanager
def text_request_slot(owner_id, model, checkpoint=lambda: None):
    """Acquire before journaling a request, so paused waiters are never unknown."""
    key = _scope(owner_id, model)
    with _gate_lock:
        gate = _gates.setdefault(key, _Gate())
        gate.users += 1
    acquired = False
    try:
        while not acquired:
            checkpoint()
            acquired = gate.semaphore.acquire(timeout=0.1)
        checkpoint()
        yield
    finally:
        if acquired:
            gate.semaphore.release()
        with _gate_lock:
            gate.users -= 1
            if not gate.users:
                _gates.pop(key, None)


def mark_text_repair():
    """Count only a received-answer repair; transport failures are not replayed."""
    stats = _batch_stats.get()
    if stats is not None:
        with stats['lock']:
            stats['repaired'] += 1


def _paused(error):
    # Avoid importing AgentService (which imports StudioMixin).
    return type(error).__name__ == 'StopWork'


def run_bounded(items, worker, commit, *, checkpoint, progress=None, max_workers=TEXT_CONCURRENCY):
    """Dynamically fill free slots, drain in-flight work, then raise failures.

    ``worker(item)`` runs with inherited context; ``commit(item, result)`` and
    ``progress(snapshot)`` run only on the calling coordinator. A successful
    sibling is persisted even when another task fails. No new work is submitted
    after an observed failure or pause. Already dispatched provider responses
    retain the operation journal's existing recovery semantics.
    """
    items = list(items)
    maximum = min(TEXT_CONCURRENCY, max(1, int(max_workers)))
    total, submitted, current, failed = len(items), 0, 0, 0
    pending, errors = {}, []
    stopped = Event()
    started = time.monotonic()
    stats = {'lock': Lock(), 'repaired': 0}
    token = _batch_stats.set(stats)
    last_snapshot = None

    def report(status='running'):
        nonlocal last_snapshot
        with stats['lock']:
            repaired = stats['repaired']
        snapshot = {'current': current, 'total': total, 'running': len(pending),
                    'queued': total - submitted, 'failed': failed, 'repaired': repaired,
                    'elapsed_seconds': int(time.monotonic() - started),
                    'concurrency': maximum, 'status': status}
        if progress is not None and snapshot != last_snapshot:
            progress(snapshot)
        last_snapshot = snapshot

    def invoke(item):
        try:
            checkpoint()
            return worker(item)
        except BaseException:
            stopped.set()
            raise

    def record_error(error):
        nonlocal failed
        stopped.set()
        errors.append(error)
        if not _paused(error):
            failed += 1

    try:
        report()
        with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix='studio-text') as pool:
            while submitted < total or pending:
                if not stopped.is_set():
                    try:
                        checkpoint()
                    except BaseException as error:
                        record_error(error)
                while not stopped.is_set() and submitted < total and len(pending) < maximum:
                    try:
                        checkpoint()
                    except BaseException as error:
                        record_error(error)
                        break
                    item = items[submitted]
                    future = pool.submit(copy_context().run, invoke, item)
                    pending[future] = (submitted, item)
                    submitted += 1
                report()
                if not pending:
                    break
                done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                # Inspect every completed result before filling another slot.
                for future in sorted(done, key=lambda f: pending[f][0]):
                    _, item = pending.pop(future)
                    try:
                        result = future.result()
                    except BaseException as error:
                        record_error(error)
                    else:
                        try:
                            commit(item, result)
                            current += 1
                        except BaseException as error:
                            record_error(error)
                report()
        # A pause may arrive alongside a worker failure while draining. It takes
        # priority, without discarding successful results or recorded failures.
        try:
            checkpoint()
        except BaseException as error:
            record_error(error)
        if errors:
            error = next((error for error in errors if _paused(error)), errors[0])
            report('paused' if _paused(error) else 'blocked')
            raise error
        report('completed')
    finally:
        _batch_stats.reset(token)
