#!/bin/sh
# Restore a backup made by backup.sh into an EMPTY stack (fresh volumes).
#
#   COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production \
#     ./scripts/restore.sh /backups/20261027T030000Z
#
# Stop api and worker first so nothing writes during the restore.
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

if [ "${PIKIRLER_STORAGE_BACKEND:-local}" = "s3" ]; then
  echo "restoring objects into MinIO..."
  NETWORK=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' \
    "$(dc ps -q minio)")
  docker run --rm --network "$NETWORK" -v "$(cd "$SRC" && pwd)/objects:/backup:ro" \
    -e MC_HOST_dst="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000" \
    "${MINIO_MC_IMAGE:-minio/mc:latest}" sh -c "mc mb --ignore-existing dst/$PIKIRLER_S3_BUCKET && \
      mc anonymous set none dst/$PIKIRLER_S3_BUCKET && mc mirror --quiet /backup dst/$PIKIRLER_S3_BUCKET"
else
  echo "restoring files into the media volume..."
  (cd "$SRC/objects" && tar -cf - .) \
    | dc run --rm --no-deps -T api tar -C "${PIKIRLER_LOCAL_STORAGE_PATH:-/srv/data/media}" -xf -
fi

echo "applying migrations (no-op if the dump is current)..."
dc run --rm migrate alembic upgrade head
echo "restore complete"
