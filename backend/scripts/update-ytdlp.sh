#!/bin/sh
# Rebuild the backend image with the newest yt-dlp, test a real download with
# it, and switch to it only if the test passes; otherwise keep the old image.
# Run weekly by deploy/systemd/pikirler-ytdlp-update.timer, or by hand:
#   backend/scripts/update-ytdlp.sh
set -eu
cd "$(dirname "$0")/../.."
C="docker compose -f backend/docker-compose.prod.yml --env-file backend/.env.production"
IMAGE=pikirler-backend:prod
stamp=$(date -u +%Y-%m-%dT%H:%MZ)

docker image tag "$IMAGE" pikirler-backend:previous
$C build --build-arg YTDLP_REFRESH="$stamp" api
if $C run --rm --no-deps -T worker pikirler check-media; then
    $C up -d api worker
    echo "$stamp switched to the rebuilt image"
else
    docker image tag pikirler-backend:previous "$IMAGE"
    echo "$stamp the rebuilt image failed the download test; kept the previous one" >&2
    exit 1
fi
