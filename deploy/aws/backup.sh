#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
for database in trust op_service; do
    target="backups/${database}-${stamp}.dump"
    docker compose exec -T db pg_dump -U trust -d "$database" -Fc > "$target.tmp"
    mv "$target.tmp" "$target"
    printf '%s\n' "$target"
done
