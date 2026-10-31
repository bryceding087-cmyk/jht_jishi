"""Bounded, persistent retries for saving completed videos (never generation)."""
import logging
import threading
import time

from data_access import access_scope


class VideoFileRecovery:
    def __init__(self, host):
        self.h = host
        self.lock = threading.Lock()
        self.active = set()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.thread = threading.Thread(target=self.loop, name='video-file-recovery', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)

    def process(self, tid):
        with access_scope(None):
            task = self.h.raw_record('task', tid)
            if (not task or task.get('deleted_at') or task.get('status') != 'done'
                    or task.get('video_cache_status') != 'pending'):
                return
            with self.h.worker_scope(task):
                self.h.finalize_video_task(tid, refresh_url=True)

    def dispatch(self):
        for task in self.h.listing('task'):
            retry_at = task.get('video_cache_retry_at')
            if (task.get('status') != 'done' or task.get('deleted_at')
                    or task.get('video_cache_status') != 'pending'
                    or not retry_at or retry_at > time.time()):
                continue
            tid = task['id']
            with self.lock:
                if len(self.active) >= 2:
                    break
                if tid in self.active:
                    continue
                self.active.add(tid)
            def run(task_id=tid):
                try:
                    self.process(task_id)
                except Exception:
                    logging.getLogger(__name__).exception('Video file recovery failed')
                finally:
                    with self.lock:
                        self.active.discard(task_id)
            threading.Thread(target=run, name='video-file-save', daemon=True).start()

    def loop(self):
        with access_scope(None):
            while not self.stopped.is_set():
                self.wake.clear()
                try:
                    self.dispatch()
                except Exception:
                    logging.getLogger(__name__).exception('Video file retry dispatch failed')
                self.wake.wait(2)
