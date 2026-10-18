"""Usage: python scripts/migrate_sqlite.py --source data/jianhuitong.db.

Set JIANHUITONG_DATABASE_URL (or DATABASE_URL) to the new, empty PostgreSQL database. The source SQLite and
uploads remain unchanged. Repeating the command verifies the identical import.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage.migrate import migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        report = migrate(args.source, os.environ.get('JIANHUITONG_DATABASE_URL') or os.environ.get('DATABASE_URL'), verify_only=args.verify_only)
    except Exception as error:
        # Driver error strings can contain connection details and SQL values.
        # Report only our own preflight errors; never dump a target DSN or rows.
        detail = str(error) if type(error) is ValueError else type(error).__name__
        print('Migration failed: ' + detail, file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
