"""Wait for the external PostgreSQL container, then run the single-worker app."""
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import load_deployment_config
from sqlalchemy import create_engine, text


def main():
    load_deployment_config()
    engine = create_engine(os.environ['JIANHUITONG_DATABASE_URL'], connect_args={'connect_timeout': 3})
    for attempt in range(30):
        try:
            with engine.connect() as db:
                db.execute(text('SELECT 1'))
            break
        except Exception:
            if attempt == 29:
                print('PostgreSQL is not ready; check the database container and runtime configuration.', flush=True)
                return 1
            time.sleep(2)
    engine.dispose()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parents[1] / 'server.py')])


if __name__ == '__main__':
    raise SystemExit(main())
