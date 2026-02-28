# План улучшения функционала Open NotebookLM

Статус: предложен  
Дата: 2026-02-26

## Цель

Усилить продукт не за счёт новых "витринных" функций, а за счёт:

- качества ответов и цитирования (RAG),
- удобства длительной исследовательской работы (проекты/коллекции),
- управляемости генерации подкастов,
- надёжности долгих задач и batch-процессов,
- контроля качества через тесты и регрессии.

## Текущее состояние (по коду)

Уже реализовано:

- загрузка документов/URL, ingest, RAG, summary;
- чат (обычный, conversational, compare);
- voice Q&A (включая streaming/VAD/barge-in);
- генерация подкаст-скриптов (сценарии, role-LLM, turn-taking);
- TTS/audio jobs, экспорты, batch-run;
- UI настроек (LLM/TTS/music/postprocess/style/scenarios).

## Приоритеты (по ROI)

### P1. Качество RAG и ответов (максимальный эффект)

Почему:

- это влияет почти на все режимы: summary, chat, compare, voice Q&A, podcast.

Что улучшить:

- Hybrid retrieval: объединить vector search + lexical search (BM25/TF-IDF).
- Reranker для top-N фрагментов перед отправкой в LLM.
- Metadata-rich chunks: сохранять `page`, `section`, `heading`, `source_type`.
- Специализированные retrieval-профили: `quote`, `overview`, `technical`, `formulas`.
- Улучшенный чанкинг для таблиц/списков/формул (не только sentence split).

Критерии готовности:

- заметно меньше нерелевантных фрагментов в top-k;
- лучшее качество цитат в chat/voice Q&A;
- нет деградации скорости на малых документах.

### P2. Проекты/коллекции документов (режим "ноутбука")

Почему:

- сейчас UX в основном документ-центричный; для исследований нужен уровень выше документа.

Что добавить:

- сущность `project/notebook`;
- привязка документов к проекту;
- сохранённые подборки документов для сравнения;
- заметки пользователя (notes) и pinned Q&A;
- проектные настройки (модель, стиль, сценарий, параметры ответа).

Критерии готовности:

- можно открыть проект и продолжить работу с тем же набором документов;
- compare/chat используют набор документов проекта без ручного перевыбора.

### P3. Итеративное редактирование подкаста

Почему:

- генерация уже сильная, но финализация занимает время без точечной регенерации.

Что добавить:

- регенерация выбранной реплики/сцены;
- "lock" для вручную отредактированных реплик;
- редактор outline (если включён `turn_taking`);
- главы/таймкоды и экспорт chapters;
- сравнение версий скрипта (v1/v2).

Критерии готовности:

- пользователь может исправить часть скрипта без полной регенерации;
- TTS/audio pipeline работает с частично ручным скриптом без ошибок.

### P4. Надёжность job-пайплайна и восстановление

Почему:

- длинные задачи (ingest/audio/batch) требуют отмены, повтора и восстановления после рестарта.

Что улучшить:

- статусы задач: `queued/running/done/error/cancelled/retrying`;
- `Cancel`/`Retry` endpoints + UI-кнопки;
- восстановление/очистка "зависших" jobs при старте;
- хранение артефактов и метаданных задач отдельно;
- лимиты и очереди по lane с явным отображением в UI.

Критерии готовности:

- можно отменить аудио/batch job;
- retry не дублирует артефакты бесконтрольно;
- после рестарта UI показывает корректный статус задач.

### P5. Качество ingest/парсинга (PDF/DOCX/PPTX)

Почему:

- слабый ingest ухудшает весь RAG и цитирование.

Что улучшить:

- извлечение таблиц и подписи к изображениям;
- чистка колонтитулов/сносок/повторов;
- сохранение структуры документа (заголовки/разделы);
- page anchors / section anchors для точных ссылок в UI;
- режимы OCR (быстрый/точный) и явная индикация OCR-фрагментов.

Критерии готовности:

- меньше "мусорного" текста в индексах;
- точнее источники и ссылки на страницы/секции.

### P6. Тесты и регрессии (обязательная база)

Почему:

- проект уже сложный; без регрессий сложно развивать сценарии/voice/RAG без поломок.

Что добавить:

- backend smoke/e2e: `upload -> ingest -> summary -> script -> audio`;
- тесты на стабильность JSON-скрипта (`turn_taking`);
- RAG regression набор (контрольные документы + expected chunks);
- frontend smoke (основные пользовательские сценарии);
- CI-команды для быстрого локального запуска smoke-тестов.

Критерии готовности:

- изменения в RAG/podcast не принимаются без базовых smoke/regression checks;
- есть минимум 1 автотест на каждый критичный pipeline.

## Рекомендуемый roadmap (2 недели)

## Неделя 1 (максимальный пользовательский эффект)

1. Улучшение RAG (P1) — MVP
- добавить lexical retrieval (BM25/TF-IDF fallback/merge);
- объединить результаты с vector retrieval;
- внедрить rerank top-20 -> top-6;
- расширить metadata chunk (page/section, где доступно).

2. Тесты на RAG/QA (P6, частично)
- сделать набор контрольных документов;
- зафиксировать метрики (`precision@k`/hit-rate по цитате на smoke-уровне).

3. UX-улучшение источников в UI
- показать page/section рядом с citations;
- фильтр/режим ответа: `точно с цитатами` / `обзор`.

## Неделя 2 (удобство и надёжность)

1. Job reliability (P4) — MVP
- `cancel/retry` для `audio` и `batch`;
- корректные статусы `queued/cancelled`;
- обработка незавершённых jobs после рестарта backend.

2. Итеративное редактирование подкаста (P3) — MVP
- регенерация одной реплики;
- lock/unlock строки;
- повторный preview/TTS для одной реплики.

3. Начало project/notebook уровня (P2) — foundation
- backend store для `projects`;
- UI: создать проект, добавить документы, открыть проект.

## Технические точки интеграции (ориентиры по файлам)

Backend:

- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/services/rag_service.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/services/ingest_service.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/services/podcast_service.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/routers/api.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/job_manager.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/document_store.py`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/backend/app/chat_store.py`

Frontend:

- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/App.jsx`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/api/client.js`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/components/ChatPanel.jsx`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/components/ScriptPanel.jsx`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/components/DocumentList.jsx`
- `/Users/ryltsin/Documents/GitHub/open-notebooklm/frontend/src/components/JobPanel.jsx`

## Предлагаемый порядок реализации (если делать по одному инкременту)

1. `RAG quality` (hybrid + rerank + metadata)
2. `RAG regression tests`
3. `Job cancel/retry + queued/cancelled statuses`
4. `Podcast line regenerate + lock`
5. `Projects/collections foundation`
6. `Ingest structure/table extraction improvements`

## Риски и ограничения

- Локальный режим и разные LLM-модели в LM Studio могут давать нестабильные форматы вывода (особенно для `turn_taking`).
- Улучшение качества retrieval может увеличить latency без rerank/кэша.
- Переход с JSON-store на SQLite стоит планировать отдельно, чтобы не ломать текущие данные.

## Минимальный следующий шаг (рекомендуется начать с него)

Сделать `P1 (RAG quality) MVP`:

- hybrid retrieval,
- rerank,
- metadata в chunk/sources,
- smoke/regression на 2-3 типовых документа.

## Журнал выполнения

- [x] 2026-02-26: Старт `P1 (RAG quality MVP)` на backend. Реализован `hybrid retrieval` в `rag_service` (vector + lexical BM25-подобный поиск + эвристический rerank), добавлено кеширование lexical-индекса по документу и best-effort metadata extraction (`page`, `section_path`) на этапе индексации/выдачи. В `ingest_service` для PDF добавлены явные маркеры страниц (`[PDF page N]`) для восстановления ссылок на страницы в RAG-источниках при повторном ingest. Проверка: `py_compile` для изменённых файлов.
- [x] 2026-02-26: Добавлен минимальный `RAG regression/smoke` набор (`backend/tests/test_rag_hybrid.py`) без зависимости от реального Chroma/embeddings (через import-stubs): проверка metadata extraction (`page`, `section_path`), hybrid rerank и fallback-контракта `retrieve()`. Добавлен `make rag-smoke`. Проверено: `make rag-smoke` (4 теста, OK).
- [x] 2026-02-26: Реализован MVP `cancel/retry` для `audio` и `batch` jobs. Backend: новые endpoints `/api/jobs/{id}/cancel` и `/api/jobs/{id}/retry`, статус `cancelled`, cooperative cancel checks в TTS/batch flow, сохранение `job_type/recipe` для retry. Frontend: `JobPanel` получил кнопки `Отменить`/`Повторить`, `useJobPoller` останавливается на `cancelled`. Проверено: `py_compile` backend, `vite build`, `make rag-smoke`.
- [x] 2026-02-26: Реализован MVP `Podcast line regenerate + lock`. Backend: endpoint `/api/podcast_script/{document_id}/regenerate_line` перегенерирует одну реплику по текущему скрипту (соседние реплики) и RAG-контексту документа, сохраняет обновлённый скрипт. Frontend: в `ScriptPanel` добавлены кнопки `Перегенерировать` и `Lock/Unlock` для каждой реплики; lock блокирует построчное редактирование и регенерацию (локально в UI). Проверено: `py_compile` backend, `vite build`.
- [x] 2026-02-26: Реализован foundation `Projects/collections` (подборки документов). Backend: новый `project_store` (`data/projects.json`) и CRUD endpoints `/api/projects*`, плюс авто-удаление `document_id` из подборок при удалении документа. Frontend: `DocumentList` получил UI для создания/выбора/сохранения/удаления подборок и применения выбора документов из подборки (под compare/batch). Проверено: `py_compile` backend, `vite build`, `make rag-smoke`.
- [x] 2026-02-26: Улучшен UX точечной регенерации реплик в `ScriptPanel`: добавлено поле пользовательской инструкции для `Перегенерировать` (пробрасывается в backend endpoint `regenerate_line`) и сделано сохранение `Lock`-состояния по документу в `localStorage` (переживает перезагрузку страницы). Проверено: `vite build`.
- [x] 2026-02-26: Сделан UI-level `active project` контекст. `DocumentList` теперь сохраняет выделение документов и выбранную подборку в `localStorage` (переживает размонтирование панели/перезагрузку), а `App` получает `onProjectContextChange` и показывает активную подборку в колонке `Studio` с признаком `выбор сохранён/изменён`. Проверено: `vite build`.
- [x] 2026-02-26: В `ScriptPanel` добавлена кнопка автопочинки по TTS-подсказкам: `Применить нормализацию` (и для `long_line` — с попыткой разбить реплику на 2 строки того же спикера). Кнопка использует `qualityLine.suggestion`, сохраняет скрипт через `importScript`, учитывает `Lock`. Проверено: `vite build`.
- [x] 2026-02-26: Закрыт UI-пункт по RAG-ответам в `ChatPanel`: добавлен явный кнопочный переключатель режима ответа (`Баланс` / `С цитатами` / `Обзор` / `Формулы`) поверх существующего `question_mode`, а на чипах citations/voice-sources теперь показываются `стр.` и `section` (компактно, без открытия превью). Проверено: `vite build`.
- [x] 2026-02-26: Добавлено восстановление/очистка зависших `jobs` после рестарта backend в `job_manager`: записи со статусом `running` при старте процесса переводятся в `error`, `pending` — в `cancelled`, с понятным сообщением и возможностью `Retry`. Recovery выполняется автоматически после загрузки `jobs.json`. Проверено: `py_compile`.
- [x] 2026-02-26: Доведена привязка `Chat compare` к активной подборке как дефолту. `App` передаёт `activeProjectContext` в `ChatPanel`; при входе в режим `Сравнение` чат теперь применяет точный состав документов из текущего выбора `Sources` (подборка/подборка+изменения), а не только добавляет их поверх старого выбора. В UI compare добавлен блок с активной подборкой и кнопка `Применить подборку`. Проверено: `vite build`.
- [x] 2026-02-26: `P5 ingest` MVP (структура + чистка PDF): в `ingest_service` добавлена очистка повторяющихся верхних/нижних строк PDF (колонтитулы/номера страниц) по межстраничной эвристике, а также markdown-разметка heading-like строк для лучшего `section_path` в RAG. Для DOCX добавлено сохранение заголовков по стилям (`Heading`/`Заголовок`) как `#`-заголовков. Добавлен unit-тест `backend/tests/test_ingest_cleanup.py` (3 теста). Проверено: `py_compile`, `unittest`.
- [x] 2026-02-26: `P6` (частично) — добавлен backend API smoke/e2e тест ключевого пайплайна `upload -> ingest -> summary -> podcast_script -> podcast_audio -> jobs/{id}` через `FastAPI TestClient` с mock/stub зависимостей (без LM Studio/TTS/Chroma). Тест изолирует `inputs/outputs/data` во временной директории и проверяет сохранение документа/статус job `done`. Файл: `backend/tests/test_api_smoke_pipeline.py`. Добавлена команда `make api-smoke`.
- [x] 2026-02-26: `P6` (частично) — добавлен набор тестов на устойчивость `turn_taking` JSON/outline-пайплайна: восстановление "грязного" script JSON (`_parse_script_json`), маппинг alias/translit имён ролей, нормализация/масштабирование outline JSON (`_parse_turn_outline_json`) и проверки `validate_script_completeness(..., mode="turn_taking")`. Файл: `backend/tests/test_turn_taking_json_stability.py` (5 тестов). Добавлена команда `make turn-json-smoke`.
- [x] 2026-02-26: `P5` (частично) — добавлен `PDF table extraction MVP` в `ingest_service` (через `pdfplumber.extract_tables()`): таблицы сериализуются в RAG-friendly текстовые блоки с маркерами страницы/таблицы (`[PDF page N]`, `[PDF table K]`, `Колонки`, `Строка i`), ограничением по числу таблиц/строк/столбцов и фильтром на вырожденные/дублирующие page text таблицы. Табличные блоки встраиваются в текст страницы при ingest. Расширен `backend/tests/test_ingest_cleanup.py` (5 тестов).
- [x] 2026-02-26: `P4` (частично) — добавлена видимость очереди/лимитов jobs по lane. Backend: `/api/jobs/{id}` теперь возвращает runtime-метрики (`lane_limit`, `lane_running`, `lane_pending`, `queue_position`), добавлен служебный endpoint `/api/jobs/lanes/summary`; frontend `JobPanel` показывает lane, число выполняющихся/ожидающих задач и позицию в очереди для `pending` job. Добавлен unit-тест `backend/tests/test_job_queue_visibility.py` и расширен `test_api_smoke_pipeline.py` на новые поля job-status. Проверено: `py_compile`, `unittest`, `vite build`.
- [x] 2026-02-26: `P5` (частично) — добавлен `PPTX table extraction MVP` в `ingest_service`: таблицы из `shape.table` сериализуются в текстовые блоки (`Слайд N`, `[PPTX table K]`, `Колонки`, `Строка i`) и добавляются в ingest рядом с обычным slide text; поддержаны вложенные group-shapes, table-shape не дублируется через `shape.text`. Расширен `backend/tests/test_ingest_cleanup.py` до 7 тестов (включая `parse_pptx` с fake shapes). Проверено: `py_compile`, `unittest`.
- [x] 2026-02-26: `P6` (частично) — добавлен `frontend smoke` контур на `Vitest + Testing Library` (без тяжёлого браузерного e2e): настроен `jsdom`-раннер во `frontend` (`vite.config.js`, `src/test/setup.js`), добавлены smoke-тесты для `SummaryPanel` (render/copy/toggle sources), `JobPanel` (queue/lane UI + cancel/retry actions) и `ScriptPanel` (кнопка `Применить нормализацию и разбить`, вызов `importScript` с разбиением реплики). Добавлена команда `make frontend-smoke`. Проверено: `npm test`, `vite build`.
- [x] 2026-02-26: `P6/P5/P3` — добавлен `ChatPanel` frontend smoke (`режим ответа` + citations/page/section preview), улучшен ingest (`anchors/captions/dedupe` MVP для PDF/PPTX таблиц: `Якорь`, best-effort `Подпись`, дедупликация одинаковых таблиц/повторных текстовых shape-блоков), и реализован backend-persist `lock` для реплик подкаста через endpoints `/api/podcast_script/{document_id}/locks` (frontend `ScriptPanel` синхронизирует locks с backend с fallback на `localStorage`). Расширены тесты `backend/tests/test_ingest_cleanup.py` и `backend/tests/test_api_smoke_pipeline.py`.
- [x] 2026-02-26: `P5` (следующий инкремент) — добавлены `figure captions + anchors` в ingest для PDF/PPTX. Подписи вида `Рисунок ...` / `Fig. ...` теперь сериализуются в отдельные RAG-friendly блоки с маркерами (`[PDF figure N]` / `[PPTX figure N]`) и `Якорь: pdf:pX:fig:Y` / `pptx:sX:fig:Y`, чтобы улучшить поиск/цитирование по рисункам. Расширен `backend/tests/test_ingest_cleanup.py` (проверки anchors/captions для figure blocks). Проверено: `py_compile`, `unittest`.
- [x] 2026-02-26: `P5` (следующий инкремент) — metadata из ingest-структурированных блоков (`table/figure`) протянута в RAG/citations: `rag_service` теперь извлекает и возвращает `source_type`, `anchor`, `caption` вместе с `page/section_path`, а `podcast_service` включает эти поля в `chat/voice` citations. `ChatPanel` обновлён: citation chips помечают `таблица/рисунок`, а в preview показываются `source_type`, `anchor` и `Подпись`. Проверено: `py_compile`, `vite build`.
- [x] 2026-02-26: `P5` (следующий инкремент) — убрано дублирование подписей (`caption`) в ingest: если `Таблица ...` / `Рисунок ...` уже сериализованы в отдельные structured blocks (`table/figure`), соответствующие caption-линии удаляются из основного текста PDF-страницы и PPTX-слайда. Это снижает шум и уменьшает дубли в retrieval. Расширен `backend/tests/test_ingest_cleanup.py` (helper + `parse_pptx` counts). Проверено: `py_compile`, `unittest`.
- [x] 2026-02-26: UX/Chat — обычный чат (`Q&A` и `Conv RAG`) теперь может работать по подборке/выбору документов из `Sources`, если выбрано 2+ документа (раньше multi-doc был только в режиме `Сравнение`). В `ChatPanel` добавлена индикация контекста чата (`подборка / N документов`), при этом voice-режим остаётся привязан к одному текущему документу. Проверено: `vite build`.
- [x] 2026-02-26: UX/Chat — добавлен явный переключатель контекста текстового чата в `ChatPanel` (`Авто / Один документ / Подборка`) с сохранением в `localStorage`. `Q&A` и `Conv RAG` теперь можно принудительно запускать только по текущему документу или только по подборке; для режима `Подборка` добавлена понятная ошибка/подсказка, если в `Sources` выбрано меньше 2 документов. Проверено: `vite build`.
- [x] 2026-02-26: UX/Voice — голосовой режим (`Voice Q&A` / `Voice Conv RAG`) теперь тоже может работать по подборке/выбору документов из `Sources` (multi-doc) в режимах `Q&A` и `Conv RAG`. Frontend `ChatPanel` передаёт `document_ids` текущего контекста в `voice_qa` stream, backend `voice_qa`/`voice_qa_stream` и `voice_qa_service.run_voice_qa(...)` поддерживают multipart-поле `document_ids` (JSON/CSV) с fallback на path `document_id`. В режиме `Сравнение` voice по-прежнему выключен. Проверено: `py_compile`, `vite build`.
- [x] 2026-02-26: `P6` (frontend smoke, регрессии) — добавлен тест в `ChatPanel.smoke.test.jsx` на кейс `Enter` в текстовом поле: текстовый запрос должен вызывать `queryChat`, не запускать `consumeVoiceQaStream` и не открывать voice modal (`Голосовой режим`) даже во время `busy`. После фикса условия `showVoiceModal` полный `make frontend-smoke` снова проходит стабильно (4 файла / 6 тестов). Примечание: предупреждение Node про `--localstorage-file` остаётся, но тесты зелёные.
- [x] 2026-02-26: `P5` (section anchors) — добавлены `Якорь: ...:sec:N` для обычных heading-блоков (не только `table/figure`) в ingest plain-text pipeline: PDF (после heading-аннотаций по страницам), DOCX (markdown heading по стилям) и PPTX (heading-like строки слайда). Это позволяет `rag_service` автоматически протягивать `anchor` в citations для обычных разделов. Расширен `backend/tests/test_ingest_cleanup.py` (helper + `parse_pptx` anchor check). Проверено: `py_compile`, `unittest`.
- [x] 2026-02-26: UX polish — приведён в порядок `ChatPanel`: шапка разбита на toolbar-ряды (режимы/очистка отдельно от настроек ответа), блок контекста получил явную структуру (`Контекст текстового чата` + режим + summary), voice-controls разделены на `actions` и `switches`, добавлен более читаемый helper-блок, улучшены карточки сообщений и оформлен input-блок (caption + shortcut + компактная раскладка textarea/button). Проверено: `vite build`, `ChatPanel.smoke`.
- [x] 2026-02-27: UX refactor — `ChatPanel` очищен от перегруза: часть action-кнопок переведена в иконки с `title/aria-label` (`Очистить`, `Остановить озвучку`, `Повторить`, `Ассистент`), режим ответа/длина/контекст переведены в dropdown/compact chips, редкие настройки перенесены в раскрываемые блоки (`Расширенные настройки чата`, `Опции голоса`). Проверено: `vite build`, `make frontend-smoke`.
- [x] 2026-02-27: `P2 notebook` — реализован API + UI для `notes/pinned Q&A`. Backend: добавлены endpoints `/api/projects/{id}/notebook`, `/api/projects/{id}/notes`, `/api/projects/{id}/pins`, `/api/projects/{id}/pins/{pin_id}` поверх `project_store` notebook-методов. Frontend: в `DocumentList` добавлен блок `Notebook подборки` (редактирование заметок и список/удаление закреплённых Q&A), в `ChatPanel` добавлена кнопка `В notebook` для ответов ассистента (pin в активную подборку). Добавлен backend smoke `tests/test_project_notebook_api.py` + `make project-notebook-smoke`. Проверено: `make project-notebook-smoke`, `make api-smoke`, `vite build`, `make frontend-smoke`.
- [x] 2026-02-27: UX refactor (единый стиль) — `ScriptPanel` и `JobPanel` приведены к единому clean-layout с компактными toolbar-паттернами: редкие операции вынесены в `details` (`Инструменты`, `Точечная регенерация`, `Артефакты`), line-actions в `ScriptPanel` уплотнены в icon-кнопки с tooltip/aria-label, блоки метрик/таймкодов сделаны collapsible; `JobPanel` получил чипы статуса/очереди и более компактный action-row. Проверено: `vite build`, `make frontend-smoke`.
- [x] 2026-02-27: `P3` закрыта версионность скрипта (backend + UI). Backend: централизовано сохранение скрипта с авто-снимками в `script_meta.versions` (`id/created_at/reason/hash/script`, cap 24), добавлены endpoints `/api/podcast_script/{document_id}/versions`, `/versions/{version_id}`, `/versions/compare`, `/versions/{version_id}/restore`. Frontend: в `ScriptPanel` добавлен блок `Версии скрипта` (выбор v1/v2, сравнение, восстановление). Расширен smoke `backend/tests/test_api_smoke_pipeline.py` на lifecycle версий (list/import/compare/restore). Проверено: `py_compile`, `make api-smoke`, `make frontend-smoke`, `vite build`.
- [x] 2026-02-27: `P2` (project settings) — добавлены проектные настройки уровня подборки: backend endpoints `/api/projects/{id}/settings` (get/put) и персист в `project_store` (`chat` + `script` defaults). Frontend `DocumentList` получил блок «Настройки проекта», а `ChatPanel` и `ActionBar` подхватывают defaults активной подборки (режим/длина/strict/scope, параметры скрипта). Добавлен backend smoke `test_project_notebook_api.py::test_project_settings_roundtrip`. Проверено: `py_compile`, `unittest`, `vite build`.
- [x] 2026-02-27: `P4` (job reliability, finish) — добавлен статус `retrying`, endpoint `retry` переводит родительскую задачу в `retrying`, а артефакты задач вынесены в отдельное хранилище `data/jobs_artifacts.json` (metadata остаётся в `jobs.json`). Обновлены `job_manager`/`JobPanel`/poller и `Makefile clean`. Добавлены тесты: `test_job_queue_visibility` (separate artifacts store), расширен `test_api_smoke_pipeline` (retrying статус). Проверено: `unittest`, `vite build`.
- [x] 2026-02-27: `P5` (OCR modes + indication) — добавлены runtime OCR-настройки `GET/PUT /api/settings/ocr` (`fast/accurate`, lang, limits), ingest использует эти параметры для PDF/DOCX OCR, а `rag_service` помечает OCR-фрагменты (`source_type=ocr_pdf|ocr_docx`). Frontend: `SettingsPanel` секция OCR, `ChatPanel` citation chips явно показывают `OCR`. Расширен `test_rag_hybrid` на OCR metadata. Проверено: `py_compile`, `unittest`, `vite build`.
