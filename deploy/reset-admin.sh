#!/usr/bin/env bash
set -euo pipefail
read -r -s -p 'New admin password (at least 12 characters): ' first
printf '\n'
read -r -s -p 'Confirm password: ' second
printf '\n'
[[ "$first" == "$second" && ${#first} -ge 12 && ${#first} -le 256 ]] || {
  echo 'Passwords must match and contain 12 to 256 characters.' >&2; exit 1;
}
printf '%s\n' "$first" | jht-docker exec -i jht-app python /app/deploy/set_admin_password.py
unset first second
