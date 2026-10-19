"""Production launcher; isolated app imports may still use test SQLite databases."""
import argparse
import json
from pathlib import Path
import os
import socket
import subprocess
import sys

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
import uvicorn
from model_gateway import LOCAL_MODEL_ORIGIN, MODEL_ORIGIN_ENV


ROOT = Path(__file__).resolve().parent


def load_deployment_config(root=ROOT, *, model_origin_default=LOCAL_MODEL_ORIGIN):
    # Either explicitly supplied process variable takes precedence over .env.
    explicit_database = os.getenv('JIANHUITONG_DATABASE_URL') or os.getenv('DATABASE_URL')
    load_dotenv(Path(root) / '.env', override=False)
    os.environ[MODEL_ORIGIN_ENV] = (os.getenv(MODEL_ORIGIN_ENV, '').strip().rstrip('/')
                                    or model_origin_default)
    database_url = explicit_database or os.getenv('JIANHUITONG_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError('未配置 PostgreSQL。请运行 scripts/setup_local_postgres.ps1，或在 .env 配置 JIANHUITONG_DATABASE_URL。')
    try:
        normalized = 'postgresql://' + database_url[len('postgres://'):] if database_url.startswith('postgres://') else database_url
        parsed = make_url(normalized)
        if parsed.drivername not in ('postgresql', 'postgresql+psycopg') or not parsed.host or not parsed.database:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except (ArgumentError, TypeError, ValueError):
        # Configuration values may contain passwords; never print the original exception.
        raise ValueError('正式启动需要有效的 PostgreSQL 连接配置，不能使用 SQLite 或其他数据库类型。') from None
    os.environ['JIANHUITONG_DATABASE_URL'] = database_url
    return parsed


def database_mode(database_url, root=ROOT):
    config_path = Path(root) / 'data' / 'postgresql.local.json'
    # libpq query parameters can override host/user/service, so do not manage
    # an instance for anything beyond the installer-generated local URL.
    if os.name != 'nt' or database_url.query or not config_path.is_file():
        return 'external-postgres'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        matches = (database_url.host == config['host'] == '127.0.0.1'
                   and database_url.port == config['port'] == 55432
                   and database_url.database == config['database'] == 'jianhuitong'
                   and database_url.username == config['username'] == 'jianhuitong_app'
                   and Path(config['data_directory']).resolve() == (Path(root) / 'data' / 'postgresql').resolve())
    except (KeyError, TypeError, ValueError, OSError):
        return 'external-postgres'
    return 'local-postgres' if matches else 'external-postgres'


def start_local_database(database_url, root=ROOT):
    if database_mode(database_url, root) != 'local-postgres':
        raise ValueError('当前配置指向外部 PostgreSQL，启动器不会管理该数据库进程。')
    root = Path(root)
    data = root / 'data' / 'postgresql'
    binaries = root / '.runtime' / 'postgresql' / 'bin'
    pg_ctl, pg_isready = binaries / 'pg_ctl.exe', binaries / 'pg_isready.exe'
    if not pg_ctl.is_file() or not pg_isready.is_file() or not (data / 'PG_VERSION').is_file():
        raise ValueError('项目本地 PostgreSQL 尚未完整安装，请运行 scripts/setup_local_postgres.ps1。')
    options = {'capture_output': True, 'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    status = subprocess.run([str(pg_ctl), '-D', str(data), 'status'], timeout=10, **options)
    if status.returncode == 0:
        ready = subprocess.run([str(pg_isready), '-h', '127.0.0.1', '-p', '55432', '-q'], timeout=10, **options)
        if ready.returncode != 0:
            raise ValueError('本地 PostgreSQL 进程存在，但尚未接受连接，请检查 data/postgresql-server.log。')
        print('项目本地 PostgreSQL 已运行：127.0.0.1:55432。')
        return
    try:
        with socket.create_connection(('127.0.0.1', 55432), timeout=1):
            occupied = True
    except OSError:
        occupied = False
    if occupied:
        raise ValueError('端口 55432 已被其他进程占用，未停止或接管该进程。')
    logfile = root / 'data' / 'postgresql-server.log'
    # On Windows the daemon can inherit capture pipes, keeping communicate()
    # blocked after pg_ctl exits. Server diagnostics already go to logfile.
    started = subprocess.run([str(pg_ctl), '-D', str(data), '-l', str(logfile), '-w', '-t', '45', 'start'],
                             timeout=55, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=options['creationflags'])
    if started.returncode != 0:
        raise ValueError('项目本地 PostgreSQL 启动失败，请检查 data/postgresql-server.log。')
    print('项目本地 PostgreSQL 已启动：127.0.0.1:55432。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--check-database', action='store_true', help='Validate configuration without starting the application')
    actions.add_argument('--start-local-database', action='store_true', help='Start only the configured project-local PostgreSQL instance')
    parser.add_argument('--reload', action='store_true', help='Reload Python source during development')
    args = parser.parse_args(argv)
    try:
        database_url = load_deployment_config()
        if args.start_local_database:
            start_local_database(database_url)
            return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print('本地 PostgreSQL 启动命令未能完成，请检查安装路径和 data/postgresql-server.log。', file=sys.stderr)
        return 1
    if args.check_database:
        print(database_mode(database_url))
        return 0
    uvicorn.run('app:app', host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')),
                workers=1, reload=args.reload, reload_excludes=['data/*', '.runtime/*', 'outputs/*', 'work/*'] if args.reload else None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
