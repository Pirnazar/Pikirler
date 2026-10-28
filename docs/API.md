# API-контракт Pikirler

Базовый путь: `/api/v1/pikirler`. Полная спецификация — [openapi.json](openapi.json)
(живая версия: `GET /api/v1/pikirler/openapi.json`, UI: `/api/v1/pikirler/docs`).

## TypeScript-типы для фронтенда

Типы генерируются из OpenAPI, вручную не пишутся:

```bash
npx openapi-typescript@7 ./docs/openapi.json -o src/api/pikirler.d.ts
```

Обновить спецификацию после изменения бэкенда:

```bash
docker compose run --rm -v "$PWD/docs:/srv/app/docs" api pikirler export-openapi docs/openapi.json
```

Пример использования с `openapi-fetch`:

```ts
import createClient from "openapi-fetch";
import type { paths } from "./api/pikirler";

const api = createClient<paths>({ baseUrl: "", credentials: "include" });
const { data } = await api.GET("/api/v1/pikirler/timeline", { params: { query: { limit: 30 } } });
```

## Аутентификация в браузере

1. `POST /auth/login {email, password}` → cookie `pikirler_session` (HttpOnly) и
   `pikirler_csrf` (читаемая); в теле — `csrf_token`, `magic_word_enabled`, `unlocked`.
2. На каждый `POST/PUT/PATCH/DELETE` — заголовок `X-CSRF-Token: <значение cookie pikirler_csrf>`.
   Запросы с `credentials: "include"`.
3. Если `unlocked=false` → экран Magic Word → `POST /auth/unlock {magic_word}`.
   Любой запрос к данным до этого вернёт **423** `magic_word_required`.
4. `GET /auth/me` — состояние сессии (работает и в заблокированном состоянии).

Сервисы (Jemşit, бот): `Authorization: Bearer pks_…`, CSRF не нужен.

## Ошибки

Любой ответ не 2xx:

```json
{"error": {"code": "version_conflict", "message": "The thought was changed elsewhere; reload and retry",
           "details": {"current_version": 3}},
 "request_id": "7f1c…"}
```

`code` стабилен — по нему ветвится фронтенд; `message` — для людей;
`request_id` совпадает с заголовком `X-Request-ID` и логами. Ошибки валидации
(422 `validation_failed`) содержат `details.errors[] = {loc, msg, type}` без
эхо введённого текста.

| HTTP | Типичные `code` |
|---|---|
| 401 | `unauthorized`, `session_expired`, `invalid_credentials`, `invalid_magic_word` |
| 403 | `csrf_failed`, `insufficient_scope`, `external_ai_disabled`, `purge_not_confirmed` |
| 404 | `thought_not_found`, `attachment_not_found`, … (и для чужих объектов) |
| 409 | `version_conflict`, `tag_exists`, `project_exists`, `relation_exists`, `upload_completed` |
| 413 | `payload_too_large` |
| 422 | `validation_failed`, `empty_thought`, `attachment_not_uploaded`, `content_type_mismatch`, `checksum_mismatch`, `invalid_cursor` |
| 423 | `magic_word_required` |
| 429 | `rate_limited`, `account_locked`, `ai_quota_exceeded` (+ `Retry-After`) |
| 503 | `provider_unavailable`, `ai_disabled`, `asr_disabled`, `embeddings_disabled` |

## Пагинация

`GET /thoughts`, `/timeline*`: `?limit=1..100&cursor=…` → `{items, next_cursor}`.
Курсор непрозрачный; порядок `created_at DESC, id DESC` стабилен при вставках.
Главная лента дополнительно отдаёт `pinned` (только на первой странице).

## Мысли

- Создание: `POST /thoughts` (пример тела и ответа — как в ТЗ). Опубликованная
  мысль требует текст или хотя бы одно завершённое вложение; черновик — нет.
  `#хэштеги` в тексте становятся тегами (оригинальное написание сохраняется).
- Редактирование: `PATCH /thoughts/{id}` **обязательно** с `expected_version`;
  несовпадение → 409 с `current_version`. Каждая смена текста — ревизия.
- `DELETE` — мягкое удаление (корзина: `GET /thoughts?deleted=true`,
  восстановление `POST /thoughts/{id}/restore`; через 30 дней — окончательно).
- Треды: `POST /thoughts/{id}/replies`, `GET /threads/{id}` (дерево в порядке
  обхода, `depth`, `previous_id/next_id`), `GET /threads/{id}/timeline`.

## Загрузка файлов

```
POST /media/uploads {filename, mime_type, size, checksum_sha256}  → {id, upload_url, direct_upload_url?}
PUT  /media/uploads/{id}/content   (сырые байты; можно повторять)
POST /media/uploads/{id}/complete  → проверка размера, SHA-256 и реального типа
```

Повтор объявления с той же суммой возвращает ту же загрузку. Только после
`complete` id можно указать в `attachment_ids`. Отдача:
`GET /media/{id}/content` (изображения — без EXIF; `?original=true` — оригинал;
поддерживается `Range`), `GET /media/{id}/thumbnail`.

Голос одним запросом: `POST /voice/notes` (multipart `file`, `language?`,
`content?`, `tags?`) → мысль с аудио + задание транскрипции. Если ASR выключен,
запись всё равно сохраняется (`transcription_unavailable`).

## Поиск и AI

- `GET /search?q=` — FTS/trigram, фильтры `type, status, tag, project_id,
  thread_id, date_from, date_to`. Результат: `thought_id, snippet, created_at,
  tags, match_type (keyword|fuzzy|semantic|hybrid), score, url`.
- `POST /search/semantic`, `POST /search/hybrid` — `{query, filters, limit}`;
  поле `semantic` сообщает, участвовал ли векторный поиск.
- `GET /thoughts/{id}/similar`.
- `POST /ai/ask|summarize|related|evolution|expand|extract-actions` — все
  ответы `generated: true` + `sources[]`.

## Фоновые задания

`GET /jobs`, `GET /jobs/{id}` (`status`: pending/running/succeeded/failed/dead,
`attempts`, `last_error`, `result`), `POST /jobs/{id}/retry`.

## Экраны фронтенда → эндпоинты

| Экран | Эндпоинты |
|---|---|
| Home Timeline | `GET /timeline` |
| Composer | `POST /thoughts`, `POST /media/uploads…`, `GET /tags?q=`, `GET /projects` |
| Thought Detail | `GET /thoughts/{id}`, `/revisions`, `/relations`, `/similar` |
| Threads | `POST /thoughts/{id}/replies`, `GET /threads/{id}` |
| Bookmarks | `GET /timeline/bookmarked` |
| Topics | `GET /tags?sort=popular`, `GET /thoughts?tag=` |
| Projects | `GET /projects`, `GET /thoughts?project_id=` |
| Calendar | `GET /memories/activity?year=` |
| Memories | `GET /memories/on-this-day`, `/random`, `/monthly`, `/forgotten`, `/evolution/{id}` |
| AI Search | `POST /search/hybrid`, `POST /ai/ask` |
| Media Gallery | `GET /timeline/media`, `GET /media/{id}/thumbnail` |
| Voice Recorder | `POST /voice/notes`, `GET/PATCH /voice/transcriptions/{id}` |
| Настройки | `GET/PATCH /settings`, `/auth/sessions`, `/auth/magic-word`, `/export/*`, `/import`, `/account/purge` |
