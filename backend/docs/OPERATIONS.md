# Эксплуатация

## Production

1. `.env.production` по образцу `.env.example`: `PIKIRLER_ENVIRONMENT=production`,
   длинный `PIKIRLER_SECRET_KEY`, уникальные пароли, `PIKIRLER_CORS_ORIGINS` —
   домен фронтенда. Приложение не стартует в production с ключом-заглушкой или
   без Secure-cookie.
2. Сертификаты в `deploy/certs/` (`fullchain.pem`, `privkey.pem`), домен в
   `deploy/nginx.conf`.
3. Фронтенд: `npm ci && npm run build` в `../frontend`. nginx раздаёт
   `../frontend/dist` с того же домена, что и API (путь — `FRONTEND_DIST`),
   поэтому `PIKIRLER_CORS_ORIGINS` можно оставить пустым.
4. Сборка, миграции (отдельный управляемый шаг; дополнительно advisory lock,
   два параллельных запуска не пересекутся), запуск:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
```

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production --profile migrate run --rm migrate
```

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Наружу открыт только nginx (TLS, HSTS, лимиты на `/auth/`, логи без query
string). API, Postgres, Redis и (если включён) MinIO — во внутренней сети.
Файлы по умолчанию лежат в томе `mediadata`; с `COMPOSE_PROFILES=s3` — в MinIO. Бакет MinIO
приватный (`mc anonymous set none`), отдача файлов — через API.

Проверка: `GET /api/v1/pikirler/health/ready` (БД, Redis, хранилище).

## Бэкапы и восстановление

```bash
COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production ./scripts/backup.sh /backups
```

Создаёт `pg_dump` (custom format), копию загруженных файлов (из тома `mediadata` или бакета MinIO) и `SHA256SUMS`.
Запускать ежедневно (cron), копию — за пределы машины в зашифрованном виде
(restic/borg). Хранить: 7 ежедневных, 4 еженедельных, 12 ежемесячных.

Восстановление в пустой стек (api и worker остановлены):

```bash
COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.production ./scripts/restore.sh /backups/<stamp>
```

Независимо от инфраструктуры владелец может в любой момент выгрузить всё сам:
`GET /export/json`, `GET /export/markdown`, `POST /export/archive` (JSON +
Markdown + оригиналы файлов) и восстановить через `POST /import` (повторный
импорт безопасен — существующие id пропускаются).

## Сроки хранения

| Данные | Срок |
|---|---|
| Мысли в корзине (soft delete) | 30 дней (`PIKIRLER_SOFT_DELETE_RETENTION_DAYS`), затем удаляются задачей `cleanup` |
| Удалённые вложения | 30 дней, затем объекты и строки удаляются |
| Незавершённые загрузки | 24 часа (`PIKIRLER_UPLOAD_SESSION_TTL_HOURS`) |
| Загруженные, но ни к чему не привязанные файлы | 7 дней |
| Отклонённые проверкой файлы | удаляются сразу (объект) и при ближайшей очистке (строка) |
| Ревизии мыслей | пока существует мысль |
| Истёкшие/отозванные сессии | 30 дней |
| Успешные фоновые задания | 30 дней; неуспешные — до ручного разбора |
| `ai_usage` (без текста) | до полного удаления данных |
| Архивы экспорта | до полного удаления данных; удалять вручную после скачивания |
| Бэкапы | по ротации выше; бэкап содержит удалённые позже данные до своей ротации |
| Логи приложения | stdout контейнера; рекомендуется ротация 14 дней (`json-file` max-size/max-file) |

`POST /account/purge {password, confirm: "DELETE ALL MY THOUGHTS"}` безвозвратно
удаляет все мысли, файлы и производные данные владельца (учётная запись
остаётся). Копии в уже сделанных бэкапах исчезнут только с их ротацией.

## Логи и приватность

Логи — JSON в stdout. Пишутся только поля из белого списка: метод, *шаблон*
маршрута (`/thoughts/{thought_id}`), статус, длительность, id запроса/задания,
тип ошибки. Тексты мыслей, query string, тела запросов, сообщения исключений
и трассировки не пишутся; ключи API маскируются; ключ Gemini передаётся
заголовком, а не в URL. Аналитики и внешнего мониторинга контента нет.

## Jemşit

```bash
docker compose run --rm api pikirler create-service-client --email you@example.com --name jemshit --source jemshit --scopes thoughts:read,thoughts:write,search,voice:write,media:write
```

Токен показывается один раз; хранить в секретах Jemşit
(`Authorization: Bearer pks_…`). Отзыв: `pikirler revoke-service-client`.

| Команда | Вызов |
|---|---|
| «Jemşit, ýadyňa ýaz: …» / «Джемшит, запиши мою мысль» | `POST /integrations/jemshit/thoughts {content, origin: "owner_speech", language}` |
| «Jemşit, ýene bir pikir geldi» | `POST /integrations/jemshit/continue {content, within_minutes}` |
| «Джемшит, что я говорил об этом проекте?» | `POST /search/hybrid` (или `POST /ai/ask` при scope `ai`) |
| Голосовая заметка | `POST /voice/notes` |
| Тред | `GET /threads/{id}`, `POST /thoughts/{id}/replies` |

Пересланный текст (сообщения, страницы) отправляется с
`origin: "external_text"`: он сохраняется как данные и никогда не
интерпретируется как команда.

## ASR на отдельной GPU-машине

На машине с GPU (например, RTX 2060 с моделью Turkmen Whisper):

```bash
pip install -r asr_server/requirements.txt
```

```bash
ASR_API_KEY=<секрет> ASR_MODEL=<путь к CT2-модели или large-v3> uvicorn app:app --app-dir asr_server --host 0.0.0.0 --port 8900
```

В `.env` Pikirler: `PIKIRLER_ASR_PROVIDER=remote_whisper`,
`PIKIRLER_ASR_BASE_URL=http://<gpu-host>:8900`, `PIKIRLER_ASR_API_KEY=<секрет>`,
`PIKIRLER_ASR_IS_LOCAL=true` (машина ваша, данные не уходят третьим лицам).
Сервер обрабатывает по одной записи за раз; воркер ждёт до
`PIKIRLER_ASR_TIMEOUT_SECONDS` и повторяет при недоступности.

## Смена модели эмбеддингов

1. Поменять `PIKIRLER_EMBEDDING_MODEL`/`_DIMENSIONS`, перезапустить api и worker.
2. `pikirler reindex-embeddings --email …` — векторы новой модели строятся
   рядом со старыми; поиск сразу использует только новую модель.
3. `pikirler create-vector-index` для новой модели.
4. Старые векторы удалить SQL-запросом по `model`, когда переиндексация закончена.
