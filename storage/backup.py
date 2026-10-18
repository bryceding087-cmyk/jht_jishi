"""Pre-migration snapshots for both current SQL stores and legacy fixtures."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time


def backup_database(connect, data, prefix):
    with connect() as source:
        if hasattr(source, 'database'):
            return source.database.backup(data, prefix)
        directory = Path(data) / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{prefix}-{time.time_ns()}.sqlite3'
        with closing(sqlite3.connect(path)) as target:
            source.backup(target)
        return path
