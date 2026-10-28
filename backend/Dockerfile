# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg/ffprobe: video thumbnails and media duration. Without it media still
# uploads; previews are marked as skipped instead of pretending to succeed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --home /srv/app app
WORKDIR /srv/app

# EXTRAS=dev for the development/test image, empty for production.
ARG EXTRAS=dev
# Install only the dependencies: the code itself is imported from /srv/app
# (PYTHONPATH), so a bind-mounted checkout is always what runs, never a stale
# copy in site-packages.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
 && if [ -n "$EXTRAS" ]; then pip install ".[$EXTRAS]"; else pip install .; fi \
 && pip uninstall -y pikirler-backend \
 && rm -rf app \
 && printf '#!/bin/sh\nexec python -m app.cli "$@"\n' > /usr/local/bin/pikirler \
 && chmod +x /usr/local/bin/pikirler
ENV PYTHONPATH=/srv/app

COPY alembic.ini ./
COPY app ./app
COPY tests ./tests
# /srv/data/media holds uploaded files with PIKIRLER_STORAGE_BACKEND=local; a
# fresh named volume mounted there inherits this ownership.
RUN mkdir -p /srv/data/media && chown -R app:app /srv/app /srv/data

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/pikirler/health/live || exit 1

# Trust X-Forwarded-* only from the reverse proxy: set FORWARDED_ALLOW_IPS.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
