#!/bin/sh
# Consistent backup of Pikirler: PostgreSQL dump + uploaded files.
#
#   COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production ./scripts/backup.sh /backups
#
# Produces <dir>/<timestamp>/{pikirler.dump, objects.tar, SHA256SUMS}. Files come
# from the RustFS bucket (PIKIRLER_STORAGE_BACKEND=s3, the default) or from the
# "mediadata" volume (local). Run it daily from cron and copy the directory off
# the machine (encrypted, e.g. restic/borg).
set -eu

DEST_ROOT=${1:?usage: backup.sh <backup-dir>}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.yml}
ENV_FILE=${ENV_FILE:-.env}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DEST_ROOT/$STAMP"
mkdir -p "$DEST"
umask 077

set -a; . "./$ENV_FILE"; set +a
dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

echo "dumping database..."
dc exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner \
  > "$DEST/pikirler.dump"

if [ "${PIKIRLER_STORAGE_BACKEND:-s3}" = "s3" ]; then
  echo "exporting the storage bucket..."
  dc exec -T api pikirler dump-objects > "$DEST/objects.tar"
else
  echo "copying files from the media volume..."
  dc exec -T api tar -C "${PIKIRLER_LOCAL_STORAGE_PATH:-/srv/data/media}" -cf - . \
    > "$DEST/objects.tar"
fi

(cd "$DEST" && sha256sum pikirler.dump objects.tar > SHA256SUMS)
echo "backup written to $DEST"
