# Pikirler

**Pikirler** («мысли» по-туркменски) — приватное пространство мыслей одного
человека: личная хронологическая лента, треды, темы, проекты, голосовые заметки,
воспоминания и поиск по смыслу. Работает на вашем сервере, данные не уходят
третьим лицам без вашего явного согласия. Часть экосистемы Second Brain.

Интерфейс — на **туркменском, русском и английском** (переключатель на экране
входа и в настройках). Мысли можно писать на любом языке: поиск понимает
туркменские буквы (ý, ä, ň, ö, ü, ş, ç, ž) с ними и без них, русскую морфологию
и китайский текст.

| Папка | Что там |
|---|---|
| [`backend/`](backend/README.md) | API: FastAPI, PostgreSQL + pgvector, Redis/ARQ, Docker Compose |
| [`frontend/`](frontend/README.md) | веб-клиент: React + TypeScript + Vite |
| [`design/`](design/Pikirler.dc.html) | исходный макет интерфейса (дизайн-система Modernist) |

## Возможности

- Лента мыслей с категориями, закладками, черновиками и корзиной; треды «продолжить мысль».
- Правка с историей версий, связи между мыслями, проекты и темы через `#хэштеги`.
- Фото, видео, файлы и голосовые заметки; картинки отдаются без EXIF и геолокации.
- Поиск по словам с опечатками, по смыслу и гибридный; AI-ответы только по вашим мыслям и со ссылками на них.
- «В этот день», случайное воспоминание, календарь, итоги месяца, развитие идеи.
- Вход только для владельца (пароль + необязательное «магическое слово»), без публичной регистрации.
- Экспорт в JSON и Markdown, бэкапы, светлая и тёмная темы, мобильная вёрстка.

AI необязателен: без него всё работает, кроме ответов модели и поиска по смыслу.

---

## Установка на свой компьютер

### Что нужно

| Программа | Версия | Зачем |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) или Docker Engine + Compose v2 (Linux) | 24+ | база данных, API, хранилище файлов |
| [Node.js](https://nodejs.org/) | 20+ | веб-интерфейс |
| [Git](https://git-scm.com/) | любая | скачать проект |
| Python | 3.8+ | один раз — сгенерировать пароли (можно и вручную) |

Нужно примерно 2 ГБ свободной памяти и 3 ГБ места на диске.

### 1. Скачать проект

```bash
git clone https://github.com/Pirnazar/Pikirler.git
```

```bash
cd Pikirler
```

### 2. Создать файл настроек со случайными паролями

```bash
python backend/scripts/init_env.py --timezone Asia/Ashgabat
```

Скрипт создаст `backend/.env` из `backend/.env.example`, придумает пароли для
базы и хранилища и секретный ключ. Часовой пояс укажите свой. Файл `.env` в git
не попадает.

<details>
<summary>Без Python</summary>

Скопируйте `backend/.env.example` в `backend/.env` и замените все значения
`change_me…`:

- `POSTGRES_PASSWORD` — любой длинный пароль;
- `MINIO_ROOT_PASSWORD` и `PIKIRLER_S3_SECRET_KEY` — один и тот же пароль, не короче 8 символов (нужны, только если включите MinIO);
- `PIKIRLER_SECRET_KEY` — не меньше 64 случайных символов.
</details>

### 3. Запустить сервер

Docker Desktop должен быть запущен.

```bash
docker compose -f backend/docker-compose.yml up -d --build
```

Первая сборка занимает несколько минут. Поднимутся PostgreSQL, Redis, API и
фоновый воркер; миграции базы применятся автоматически. Загруженные файлы
хранятся в Docker-томе `mediadata`. Проверка:

```bash
docker compose -f backend/docker-compose.yml ps
```

Все сервисы должны быть `running` или `healthy`. Сервис `migrate` после запуска
завершается — это нормально.

### 4. Создать владельца

Регистрации на сайте нет: владелец создаётся командой. Она спросит пароль
(не короче 12 символов) и попросит повторить его.

```bash
docker compose -f backend/docker-compose.yml run --rm api pikirler create-owner --email you@example.com --name "Adyňyz"
```

### 5. Запустить веб-интерфейс

```bash
npm --prefix frontend install
```

```bash
npm --prefix frontend run dev
```

Откройте **http://localhost:5470** и войдите с email и паролем из шага 4.
Язык интерфейса выбирается прямо на экране входа.

### 6. Первые шаги

- **Настройки → Магическое слово** — второй замок поверх пароля: после входа
  пространство открывается только после его ввода.
- **Настройки → Часовой пояс** — нажмите «Использовать …», если он не совпадает
  с вашим.
- Напишите первую мысль: `Ctrl + Enter` публикует, `#слово` создаёт тему, `N` —
  новая мысль, `/` — поиск.

### Порты

Все порты слушают только `127.0.0.1` и меняются в `backend/.env`.

| Сервис | Порт |
|---|---|
| Веб-интерфейс (dev) | 5470 |
| API и его документация `/api/v1/pikirler/docs` | 8460 |
| PostgreSQL | 5460 |
| Redis | 6460 |
| MinIO / консоль MinIO (только если включён, см. ниже) | 9460 / 9461 |

### Остановка и запуск

```bash
docker compose -f backend/docker-compose.yml stop
```

```bash
docker compose -f backend/docker-compose.yml start
```

Данные хранятся в Docker-томах и не пропадают при остановке. Команда
`docker compose down -v` **удаляет** их — не используйте её, если не уверены.

### Хранилище файлов: диск или S3

По умолчанию фото, видео и голосовые заметки лежат в Docker-томе `mediadata`,
отдельный сервер для них не нужен. Если хотите S3-совместимое хранилище
(MinIO, Ceph, облачный S3), в `backend/.env`:

- для встроенного MinIO раскомментируйте `COMPOSE_PROFILES=s3`,
  `PIKIRLER_S3_ENDPOINT_URL` и `PIKIRLER_TEST_S3_ENDPOINT`; поставьте
  `PIKIRLER_STORAGE_BACKEND=s3`;
- Docker Hub больше не раздаёт образ `minio/mc`, поэтому укажите в
  `MINIO_IMAGE` и `MINIO_MC_IMAGE` образы из доступного вам реестра;
- для внешнего S3 профиль не нужен: укажите `PIKIRLER_S3_ENDPOINT_URL`, бакет и ключи.

---

## Подключение AI (по желанию)

Провайдеры задаются в `backend/.env`; подходят Gemini и любые
OpenAI-совместимые серверы, включая локальные Ollama, LM Studio и vLLM.

| Что | Переменные |
|---|---|
| Ответы на вопросы, анализ | `PIKIRLER_LLM_PROVIDER`, `PIKIRLER_LLM_MODEL`, `PIKIRLER_LLM_BASE_URL`, `PIKIRLER_GEMINI_API_KEY` |
| Поиск по смыслу | `PIKIRLER_EMBEDDING_PROVIDER`, `PIKIRLER_EMBEDDING_MODEL`, `PIKIRLER_EMBEDDING_DIMENSIONS` |
| Расшифровка голоса | `PIKIRLER_ASR_PROVIDER`, `PIKIRLER_ASR_BASE_URL` (например, [`backend/asr_server`](backend/asr_server/app.py) на машине с GPU) |

После изменения перезапустите API и воркер:

```bash
docker compose -f backend/docker-compose.yml up -d api worker
```

Внешнему провайдеру мысли отправляются, только если включить **Настройки → AI-функции**.
Тогда уходят только мысли, нужные для конкретного запроса, — никогда весь
дневник. Локальные модели помечаются `*_IS_LOCAL=true` и согласия не требуют.
Подробности — в [backend/README.md](backend/README.md#включение-ai-по-желанию).

---

## Установка на сервер (production)

Нужны сервер с Docker, домен и TLS-сертификат (например, от Let's Encrypt).
Снаружи открыт только nginx на портах 80 и 443; API, база и хранилище остаются
во внутренней сети Docker. Интерфейс и API работают на одном домене.

1. Склонируйте репозиторий на сервер и создайте production-настройки:

   ```bash
   python backend/scripts/init_env.py --production --timezone Asia/Ashgabat
   ```

2. Укажите домен в `backend/deploy/nginx.conf` (`server_name`) и положите
   сертификаты в `backend/deploy/certs/` как `fullchain.pem` и `privkey.pem`.

3. Соберите интерфейс:

   ```bash
   npm --prefix frontend ci
   ```

   ```bash
   npm --prefix frontend run build
   ```

4. Соберите образы, примените миграции и запустите стек:

   ```bash
   docker compose -f backend/docker-compose.prod.yml --env-file backend/.env.production build
   ```

   ```bash
   docker compose -f backend/docker-compose.prod.yml --env-file backend/.env.production --profile migrate run --rm migrate
   ```

   ```bash
   docker compose -f backend/docker-compose.prod.yml --env-file backend/.env.production up -d
   ```

5. Создайте владельца:

   ```bash
   docker compose -f backend/docker-compose.prod.yml --env-file backend/.env.production run --rm api pikirler create-owner --email you@example.com
   ```

Откройте `https://ваш-домен`. Бэкапы, сроки хранения, логи и подключение
Jemşit описаны в [backend/docs/OPERATIONS.md](backend/docs/OPERATIONS.md).

### Обновление

```bash
git pull
```

После этого повторите шаги 3 и 4: сборку интерфейса, сборку образов, миграции и
`up -d`. На своём компьютере достаточно
`docker compose -f backend/docker-compose.yml up -d --build`: миграции
применятся сами.

---

## Если что-то не работает

| Симптом | Что сделать |
|---|---|
| `Cannot connect to the Docker daemon` / `error during connect` | Запустите Docker Desktop и дождитесь, пока он загрузится. |
| `pull access denied for minio/mc` | Версия проекта до 29.10.2026 требовала MinIO. Выполните `git pull`, удалите `backend/.env` и снова запустите `python backend/scripts/init_env.py` — файлы будут храниться на диске. |
| `port is already allocated` | Порт занят другой программой — поменяйте `*_PORT` в `backend/.env` и перезапустите. Если поменяли порт API, запускайте интерфейс с `PIKIRLER_API_URL=http://127.0.0.1:<порт>`. |
| Интерфейс открывается, но вход не проходит | Проверьте, что API работает: http://127.0.0.1:8460/api/v1/pikirler/health/ready |
| Забыли пароль | `docker compose -f backend/docker-compose.yml run --rm api pikirler set-password --email you@example.com` |
| «Слишком много попыток» | Вход временно заблокирован после серии ошибок — подождите 15 минут. |
| Микрофон не работает | Браузер даёт доступ к микрофону только на `localhost` или по HTTPS. |
| AI пишет «не настроен» | Задайте провайдера в `backend/.env` (раздел «Подключение AI») и перезапустите API. |

## Для разработчиков

- Тесты бэкенда (против настоящих PostgreSQL и Redis; S3-тесты запускаются, если включён MinIO):
  `docker compose -f backend/docker-compose.yml run --rm api pytest`
- Проверка типов фронтенда: `npm --prefix frontend run typecheck`
- Документация: [архитектура](backend/docs/ARCHITECTURE.md),
  [API](backend/docs/API.md), [эксплуатация](backend/docs/OPERATIONS.md),
  [что сделано и проверено](backend/docs/STATUS.md).
- Переводы интерфейса лежат в `frontend/src/i18n/` (`en.ts` задаёт ключи;
  `ru.ts` и `tk.ts` обязаны содержать все — это проверяет компилятор).
