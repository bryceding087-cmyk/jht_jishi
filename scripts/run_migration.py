"""Run the SQLite migration using deployment environment / .env configuration."""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import load_deployment_config


if __name__ == '__main__':
    try:
        load_deployment_config(ROOT)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    runpy.run_path(str(ROOT / 'scripts' / 'migrate_sqlite.py'), run_name='__main__')
