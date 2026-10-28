# Pikirler

Приватное пространство мыслей одного владельца — часть экосистемы Second Brain.

| Папка | Что там |
|---|---|
| [`backend/`](backend/README.md) | API: FastAPI, PostgreSQL + pgvector, Redis/ARQ, MinIO, Docker Compose |
| [`frontend/`](frontend/README.md) | веб-клиент: React + TypeScript + Vite, по макету из `design/` |
| [`design/`](design/Pikirler.dc.html) | исходный макет из Claude Design (дизайн-система Modernist) |

Быстрый старт для разработки:

```bash
docker compose -f backend/docker-compose.yml up -d
```

```bash
npm --prefix frontend install
```

```bash
npm --prefix frontend run dev
```

Фронтенд откроется на http://localhost:5470, API работает на `127.0.0.1:8460`.
Продакшен описан в [backend/docs/OPERATIONS.md](backend/docs/OPERATIONS.md).
