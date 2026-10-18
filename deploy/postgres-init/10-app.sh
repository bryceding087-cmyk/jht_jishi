#!/usr/bin/env bash
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password JHT_APP_PASSWORD
SELECT format('CREATE ROLE jianhuitong_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_password') \gexec
CREATE DATABASE jianhuitong OWNER jianhuitong_app;
SQL
