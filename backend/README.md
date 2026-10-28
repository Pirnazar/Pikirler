# Pikirler — backend

Приватный микроблог одного владельца: личная хронологическая лента мыслей,
треды, ревизии, теги, проекты, медиа и голосовые заметки, многоязычный поиск
(tk / ru / en / zh), семантический поиск, AI над собственными мыслями,
воспоминания, экспорт. Модуль экосистемы Second Brain; пока — отдельный проект
со своей инфраструктурой.

> **Pikirler хранит мысли. Second Brain их понимает.** AI можно выключить,
> заменить или потерять — мысли от этого не пропадают: сохранение никогда не
> зависит от внешних провайдеров.

| Документ | О чём |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | модули, схема БД, решения и их причины |
| [docs/API.md](docs/API.md) | контракт: аутентификация, ошибки, пагинация, загрузка, TypeScript-типы |
| [docs/openapi.json](docs/openapi.json) | OpenAPI 3.1 (73 пути), сгенерирован из кода |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | деплой, бэкапы, сроки хранения, логи, Jemşit, ASR |
| [docs/STATUS.md](docs/STATUS.md) | что реализовано и проверено, что нет |

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · PostgreSQL 16 +
pgvector + pg_trgm · Redis + ARQ · MinIO (S3) · Argon2id · Pydantic v2 ·
pytest · ruff · mypy (strict).

## Быстрый старт (разработка)

Нужен только Docker. Все порты слушают `127.0.0.1`.

```bash
cp .env.example .env
```

Отредактируйте `.env`: замените все `change_me` (пароли Postgres/MinIO,
`PIKIRLER_SECRET_KEY` — 64+ случайных символов).

```bash
docker compose up -d --build
```

Поднимаются `postgres` (5460), `redis` (6460), `minio` (9460/9461), одноразовый
`migrate`, `api` (8460, авто-перезагрузка) и `worker`. Документация API:
<http://127.0.0.1:8460/api/v1/pikirler/docs>.

Публичной регистрации нет. Владелец создаётся на сервере:

```bash
docker compose run --rm api pikirler create-owner --email you@example.com --name "Ady"
```

Проверка всего стека по HTTP (API + воркер + S3):

```bash
docker compose run --rm -v "$PWD/scripts:/srv/app/scripts:ro" -e SMOKE_PASSWORD=... api python scripts/smoke_test.py http://api:8000 you@example.com
```

## Тесты и качество

```bash
docker compose run --rm api pytest
```

95 тестов против настоящих PostgreSQL (с миграциями, триггерами, pgvector,
pg_trgm), Redis, MinIO и файлов на диске. Подменяются только внешние AI/ASR-
провайдеры (`tests/fakes.py`). Тестовая БД `<POSTGRES_DB>_test` создаётся
автоматически.

```bash
docker compose run --rm api sh -c "ruff check app tests && ruff format --check app tests && mypy app"
```

## Включение AI (по желанию)

Всё работает без AI. Провайдеры подключаются переменными в `.env`:

| Возможность | Переменные | Варианты |
|---|---|---|
| Семантический поиск | `PIKIRLER_EMBEDDING_PROVIDER`, `_MODEL`, `_DIMENSIONS`, `_BASE_URL` | `gemini`, `openai_compatible` (локальный EmbeddingGemma через TEI/Ollama), `disabled` |
| Ask My Thoughts и др. | `PIKIRLER_LLM_PROVIDER`, `_MODEL`, `_BASE_URL` | `gemini`, `openai_compatible` (Ollama, LM Studio, vLLM), `disabled` |
| Транскрипция | `PIKIRLER_ASR_PROVIDER`, `_BASE_URL`, `_API_KEY` | `remote_whisper` ([asr_server/](asr_server/app.py) на GPU-машине), `openai_compatible`, `local_faster_whisper`, `disabled` |

Внешнему провайдеру данные уходят только после того, как владелец явно включит
это в настройках (`PATCH /settings {"ai_external_enabled": true}`). Локальные
модели помечаются `*_IS_LOCAL=true`.

После включения эмбеддингов:

```bash
docker compose run --rm api pikirler reindex-embeddings --email you@example.com
```

```bash
docker compose run --rm api pikirler create-vector-index
```

## Структура

```
app/
  main.py, worker.py, cli.py
  core/            config, database, security, redis, rate limit, text (многоязычная нормализация), ошибки, логи
  storage/         S3/MinIO и локальное хранилище (приватные ключи на UUID)
  api/v1/          auth, thoughts, threads, topics, projects, relations, timeline, media,
                   voice, search, memories, ai, data (экспорт/импорт/удаление), integrations, system
  modules/pikirler/
    models/        SQLAlchemy-модели
    schemas/       Pydantic-контракты
    repositories/  owner-scoped запросы к мыслям
    services/      бизнес-логика
    integrations/  провайдеры эмбеддингов, LLM, ASR
    tasks/         исполнитель фоновых заданий и обработчики
  migrations/      Alembic (+ триггер целостности цепочек мыслей)
tests/             интеграционные тесты
asr_server/        эталонный ASR-сервер для GPU-машины
deploy/            nginx
scripts/           backup.sh, restore.sh, smoke_test.py
```
