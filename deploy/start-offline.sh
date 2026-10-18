#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
bundle=$(pwd -P)
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
command -v jht-docker >/dev/null || { echo 'First run bash install-docker-offline.sh.' >&2; exit 1; }
sha256sum -c SHA256SUMS >/dev/null
chmod 0600 config/app.env config/postgres.env
jht-docker info >/dev/null
for container in jht-postgres jht-app; do
  if jht-docker container inspect "$container" >/dev/null 2>&1; then
    echo "Container $container already exists. Use jht-docker start or the documented update procedure." >&2
    exit 1
  fi
done
# Host networking avoids configuring Docker bridges or changing host firewall rules.
if command -v ss >/dev/null && ss -lntH | awk '{print $4}' | grep -Eq ':(8000|55432)$'; then
  echo 'Port 8000 or 55432 is already in use. Resolve the conflict before deployment.' >&2
  exit 1
fi
jht-docker load -i images/postgres.tar.gz
jht-docker load -i images/jht-jishi.tar.gz
# Execute the actual Linux imports, native libraries, FFmpeg and Chinese subtitle filter.
jht-docker run --rm --pull=never --network=none --entrypoint python jht-jishi:20261018 /app/deploy/container_smoke.py
jht-docker volume create jht-postgres-data >/dev/null
jht-docker volume create jht-app-data >/dev/null
jht-docker run -d --pull=never --name jht-postgres --restart=unless-stopped \
  --network=host --shm-size=256m --log-opt max-size=20m --log-opt max-file=3 \
  --env-file config/postgres.env \
  -v jht-postgres-data:/var/lib/postgresql/data \
  -v "$bundle/postgres-init:/docker-entrypoint-initdb.d:ro" \
  postgres:17-bookworm postgres -p 55432 -c listen_addresses=127.0.0.1
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if jht-docker exec jht-postgres pg_isready -h 127.0.0.1 -p 55432 -U postgres -d postgres >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[[ $ready == 1 ]] || { echo 'PostgreSQL did not become ready; inspect jht-docker logs jht-postgres.' >&2; exit 1; }
jht-docker run -d --pull=never --name jht-app --restart=unless-stopped \
  --network=host --cap-drop=ALL --security-opt=no-new-privileges \
  --log-opt max-size=20m --log-opt max-file=3 \
  --env-file config/app.env -v jht-app-data:/app/data \
  jht-jishi:20261018
for ((attempt=0; attempt<60; attempt++)); do
  if jht-docker exec jht-app python -c 'import json,urllib.request; r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health",timeout=3)); assert r["status"]=="ok" and r["storage"]=="PostgreSQL"' >/dev/null 2>&1; then
    echo 'Application and PostgreSQL health checks passed on port 8000.'
    echo 'Next: bash reset-admin.sh, then publish /market through the approved internal Web entry.'
    exit 0
  fi
  sleep 2
done
echo 'Application did not become healthy. Run jht-docker logs --tail 100 jht-app.' >&2
exit 1
