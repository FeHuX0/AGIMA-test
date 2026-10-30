#!/bin/sh
# Invoked by an operator or their scheduler; never run automatically by the app.
set -eu
umask 077
backup_dir=${1:-./backups}
mkdir -p "$backup_dir"
backup_file="$backup_dir/vector-$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f "$backup_file.tmp"' EXIT HUP INT TERM
docker compose exec -T db pg_dump -U vector -d vector -Fc > "$backup_file.tmp"
mv "$backup_file.tmp" "$backup_file"
printf '%s\n' "$backup_file"
