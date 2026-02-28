# Исследовательский ассистент

Локальная система анализа документов: загрузка, автоиндексация, чат с опорой на источники, просмотр документа, саммари, скрипт подкаста, аудио.

## Что умеет сейчас

- импорт файла или URL с автоматической индексацией;
- дедупликация файлов по хешу при повторной загрузке;
- `Q&A`, `Conv RAG`, `Сравнение`;
- режимы ответа:
  - `Только документ`
  - `Документ + знания модели`
- viewer-first поток: документ сразу открывается в правой панели;
- просмотр в режимах `Документ` / `Текст`;
- переход из чата к цитате с подсветкой источника;
- генерация саммари;
- генерация подкаст-скрипта, включая сценарии критики/дебатов;
- синтез аудио и экспорт артефактов.

## Поддерживаемые форматы

- документы: `PDF`, `DOCX`, `DOC`, `TXT`, `MD`, `HTML`, `RTF`, `ODT`, `PPTX`, `PPT`
- специализированные форматы: `DJVU`, `DJV`, `DJVY`
- URL-страницы

Preview-поведение:

- для previewable форматов используется режим `Документ`;
- для остальных или при browser-limit fallback идёт в режим `Текст`;
- длинный текст загружается оконно, а не целиком.

## Ограничения

- лимит загрузки через web UI: **150 MB на файл**;
- всё runtime-хранилище по умолчанию живёт в `./data`;
- для ответов и генерации нужен совместимый локальный LLM endpoint (обычно LM Studio).

## Архитектура

```text
Frontend (React/Vite, :3000)
  -> Backend (FastAPI, :8080)
    -> ChromaDB (:8001 на хосте / :8000 в docker-сети)
    -> local runtime data (./data)
    -> LM Studio compatible API (обычно host.docker.internal:1234/v1)
```

## Быстрый старт

### 1. Поднимите LM Studio

1. Установите [LM Studio](https://lmstudio.ai/)
2. Загрузите модель
3. Включите `Local Server`
4. Убедитесь, что endpoint доступен, обычно `http://localhost:1234/v1`

### 2. Запустите стек

```bash
cd /Users/ryltsin/Documents/GitHub/open-notebooklm
cp .env.example .env  # опционально

docker compose up -d --build
```

### 3. Откройте UI

- [http://localhost:3000](http://localhost:3000)

## Рабочий поток

1. Добавьте документ в панели `Документы`.
2. Дождитесь автоиндексации.
3. Документ откроется в `Просмотре документа`.
4. Задайте вопрос в `Q&A` или `Conv RAG`.
5. Кликните по citation, чтобы перейти к фрагменту.
6. При необходимости запустите `Саммари`, затем `Скрипт`, затем `Аудио`.

## Структура репозитория

```text
open-notebooklm/
├── backend/                 # FastAPI backend
├── frontend/                # React/Vite frontend
├── data/                    # runtime data (не часть репозитория)
├── docs/                    # docs index + specs + archive
├── media/
│   └── audio/               # локальные большие аудио-ассеты (не коммитятся)
├── docker-compose.yml
├── docker-compose.amd64.yml
├── docker-compose.target.yml
└── Makefile
```

## Где что хранится

### Исходный код

- `backend/`
- `frontend/`
- `docs/`
- `media/audio/README.md`

### Runtime-данные

- `data/inputs/` — загруженные документы
- `data/outputs/` — preview PDF, audio, exports
- `data/index/` — индекс
- `data/*.json` — runtime-метаданные

Подробности: [data/README.md](./data/README.md)

### Локальные медиа

Тяжёлые музыкальные файлы вынесены в `media/audio/`.

- они не коммитятся;
- backend Dockerfile копирует их в `/opt/audio-assets` при сборке контейнера;
- дефолтный override-трек: `zvuk-sovershennogo-volshebstva-1149.mp3`, если он лежит в `media/audio/`.

## Docker и offline bundle

Полезные команды:

```bash
make up
make down
make amd64-build
make amd64-export
make amd64-load-up
make amd64-target-up
```

Инструкция по offline bundle:
- [docs/archive/bundles/offline-amd64-bundle.txt](./docs/archive/bundles/offline-amd64-bundle.txt)

## Настройки и администрирование

В `Настройках` доступны:

- LM Studio URL / модель / температура / max tokens;
- таблица участников подкаста;
- TTS-голоса;
- LLM override по ролям;
- OCR;
- словарь произношений;
- музыкальные ассеты;
- пост-обработка аудио;
- полная очистка базы.

### Жизненный цикл удаления документа

Удаление документа должно убирать:

- запись документа;
- исходный файл;
- индекс;
- outputs;
- ссылки на job artifacts;
- ссылки на документ в проектах.

## Документация

- [docs/README.md](./docs/README.md) — индекс документации
- [docs/specs/evidence-anchor-spec-v0.md](./docs/specs/evidence-anchor-spec-v0.md) — текущая спецификация anchors/evidence
- [docs/archive/](./docs/archive/) — исторические планы и старые рабочие документы

## Development

### Frontend

```bash
cd frontend
npm install
npm run dev
npm test
npm run build
```

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```
