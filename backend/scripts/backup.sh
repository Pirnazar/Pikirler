#!/bin/sh
# Consistent backup of Pikirler: PostgreSQL dump + uploaded files.
#
#   COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production ./scripts/backup.sh /backups
#
# Produces <dir>/<timestamp>/{pikirler.dump, objects/, SHA256SUMS}. Files come
# from the "mediadata" volume (PIKIRLER_STORAGE_BACKEND=local, the default) or
# from the MinIO bucket (s3). Run it daily from cron and copy the directory off
# the machine (encrypted, e.g. restic/borg).
set -eu

DEST_ROOT=${1:?usage: backup.sh <backup-dir>}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.yml}
ENV_FILE=${ENV_FILE:-.env}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DEST_ROOT/$STAMP"
mkdir -p "$DEST/objects"
umask 077

set -a; . "./$ENV_FILE"; set +a
dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

echo "dumping database..."
dc exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner \
  > "$DEST/pikirler.dump"

if [ "${PIKIRLER_STORAGE_BACKEND:-local}" = "s3" ]; then
  echo "mirroring objects from MinIO..."
  NETWORK=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' \
    "$(dc ps -q minio)")
  docker run --rm --network "$NETWORK" -v "$(cd "$DEST" && pwd)/objects:/backup" \
    -e MC_HOST_src="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000" \
    "${MINIO_MC_IMAGE:-minio/mc:latest}" mirror --quiet "src/$PIKIRLER_S3_BUCKET" /backup
else
  echo "copying files from the media volume..."
  dc exec -T api tar -C "${PIKIRLER_LOCAL_STORAGE_PATH:-/srv/data/media}" -cf - . \
    | tar -C "$DEST/objects" -xf -
fi

(cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
echo "backup written to $DEST"
