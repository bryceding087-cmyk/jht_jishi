"""Shared AI Market models installed once, without storing session credentials."""
import time

from sqlalchemy import select
from storage import records as record_store
from storage.schema import records
from model_gateway import DOCKER_MODEL_ORIGIN


WAN_MODEL_ID = 'aimarket-wan3-video'
WAN_MODEL = {
    'id': WAN_MODEL_ID, 'name': '通义万相 Wan 3.0（AI 集市）',
    'provider': 'AI Market', 'provider_id': 'aimarket', 'model_id': 'wan3.0-video',
    'base_url': DOCKER_MODEL_ORIGIN,
    'api_path': '/v1/services/aigc/video-generation/video-synthesis',
    'query_path': '/v1/tasks/{task_id}',
    # Only the supplied gateway contract is confirmed. Extend when documented.
    'capabilities': {'resolutions': ['480p'], 'durations': [2],
                     'aspect_ratios': ['16:9'], 'modes': ['text-to-video']},
    'api_key': '',
}


def install_shared_market_video_models(connect):
    """One transaction; preserve administrator edits and intentional deletion."""
    marker_id = 'migration:shared-market-wan3-video-v1'
    with connect() as db:
        if db.sa.execute(select(records.c.id).where(records.c.id == marker_id)).first():
            return
        if not db.sa.execute(select(records.c.id).where(records.c.id == WAN_MODEL_ID)).first():
            record_store.upsert(db.sa, 'model_config', {**WAN_MODEL, 'created_at': time.time()})
        record_store.upsert(db.sa, 'migration', {'id': marker_id, 'created_at': time.time()})
