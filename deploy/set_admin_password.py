"""Read a new admin password from stdin; never put it in argv or logs."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import load_deployment_config
from auth_service import hash_password
from sqlalchemy import create_engine, text

url = load_deployment_config()
password = sys.stdin.readline().rstrip('\r\n')
if not 12 <= len(password) <= 256:
    raise SystemExit('Password must contain 12 to 256 characters.')
engine = create_engine(url)
with engine.begin() as db:
    admin_id = db.execute(text("SELECT id FROM auth_users WHERE username='admin' AND role='admin'")).scalar_one()
    db.execute(text('UPDATE auth_users SET password_hash=:password WHERE id=:id'),
               {'password': hash_password(password), 'id': admin_id})
    db.execute(text('DELETE FROM auth_sessions WHERE user_id=:id'), {'id': admin_id})
engine.dispose()
print('Admin password updated and previous admin sessions revoked.')
