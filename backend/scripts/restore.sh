#!/bin/sh
# Restore a backup made by backup.sh into an EMPTY stack (fresh volumes).
#
#   COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production \
#     ./scripts/restore.sh /backups/20261027T030000Z
#
# Stop api and worker first so nothing writes during the restore; postgres and
# (with the s3 backend) rustfs must be running.
set -eu

SRC=${1:?usage: restore.sh <backup-dir>}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.yml}
ENV_FILE=${ENV_FILE:-.env}
set -a; . "./$ENV_FILE"; set +a
dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

(cd "$SRC" && sha256sum -c --quiet SHA256SUMS) || { echo "checksum mismatch"; exit 1; }

echo "restoring database..."
dc exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  --no-owner < "$SRC/pikirler.dump"

if [ "${PIKIRLER_STORAGE_BACKEND:-s3}" = "s3" ]; then
  echo "restoring the storage bucket..."
  dc run --rm --no-deps -T api pikirler load-objects < "$SRC/objects.tar"
else
  echo "restoring files into the media volume..."
  dc run --rm --no-deps -T api tar -C "${PIKIRLER_LOCAL_STORAGE_PATH:-/srv/data/media}" -xf - \
    < "$SRC/objects.tar"
fi

echo "applying migrations (no-op if the dump is current)..."
dc run --rm migrate alembic upgrade head
echo "restore complete"
