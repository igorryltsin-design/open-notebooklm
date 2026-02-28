# План внедрения сценариев подкастов и разных LLM

Статус: в работе  
Формат отметок: `[ ]` не сделано, `[x]` сделано, `[-]` отложено

## Цель

Добавить в генерацию подкаст-скрипта:

- выбор сценария (структуры диалога),
- сценарные опции,
- (поэтапно) поддержку `роль = модель`,
- (поэтапно) пошаговую генерацию реплик (`turn-taking`).

## Точки интеграции (текущие)

- Backend request model: `backend/app/models.py`
- Backend script generation: `backend/app/services/podcast_service.py`
- API endpoints `/podcast_script/*`: `backend/app/routers/api.py`
- Frontend API client: `frontend/src/api/client.js`
- UI параметров скрипта: `frontend/src/components/ActionBar.jsx`
- UI отображения скрипта (цвета/роли): `frontend/src/components/ScriptPanel.jsx`
- UI настроек (голоса TTS): `frontend/src/components/SettingsPanel.jsx`

## Таблица сценариев

| Сценарий | Описание | Роли | MVP (одна модель) | Дальше |
|---|---|---|---|---|
| Классический обзор | Ведущий и гости разбирают документ | `host` + `guest*` | Отдельный prompt template | Можно оставить как single-pass |
| Интервью | Ведущий спрашивает, гость отвечает по документу | `host`, `guest` | Prompt с Q/A-структурой | Отдельный prompt/модель для гостя |
| Дебаты / спор | Две позиции по документу, аргументы и контраргументы | `moderator`, `speaker_a`, `speaker_b` | Prompt с ролями и позициями | Разные модели + turn-taking |
| Круглый стол | 3+ роли (практик, теоретик, скептик, модератор) | `moderator` + `role*` | Prompt с описаниями ролей | Роль = модель, реакция по истории |
| Образовательный | Учитель объясняет, ученик задаёт короткие вопросы | `teacher`, `student` | Prompt по схеме “объяснение → вопрос” | Отдельная модель для ученика (опц.) |
| Новостной дайджест | Один ведущий, короткие блоки “главное” | `host` | Monologue template | Настраиваемый тон/длина блоков |
| Расследование | Гипотезы, проверка по тексту, выводы | `host` (опц. `skeptic`) | Prompt со стадиями | Turn-taking для проверки гипотез |

## Принципы реализации

- `scenario` отвечает за структуру диалога и роли.
- `style` остаётся для тона/манеры речи.
- На первом этапе не ломаем обратную совместимость: отсутствие `scenario` = `classic_overview`.
- Выходной контракт сохраняем: JSON-массив `[{ "voice": "...", "text": "..." }]`.

## Этапы (roadmap)

### Фаза A — MVP: сценарии как разные промпты при одной модели

Цель: быстро дать выбор сценария в UI и разные prompt templates в backend без усложнения LLM-конфигурации.

- [x] Расширить `PodcastScriptRequest` в `backend/app/models.py`
- [x] Добавить поле `scenario` (default `classic_overview`)
- [x] Добавить поле `scenario_options` (словарь, default `{}`)
- [x] Прокинуть новые поля в `/podcast_script/{document_id}` (`backend/app/routers/api.py`)
- [x] Прокинуть новые поля в `/podcast_script/{document_id}/stream` (`backend/app/routers/api.py`)
- [x] Прокинуть новые поля в batch script generation flow (`backend/app/routers/api.py`)
- [x] Добавить сценарный реестр в `backend/app/services/podcast_service.py`
- [ ] Вынести prompt builder в отдельные template-функции по сценариям
- [x] Реализовать шаблон `classic_overview`
- [x] Реализовать шаблон `interview`
- [x] Реализовать шаблон `debate`
- [x] Реализовать шаблон `news_digest`
- [x] Добавить базовую валидацию количества ролей под сценарий
- [x] Сохранить совместимость TTS-friendly режима (включая второй проход rewrite)
- [x] Добавить выбор `scenario` в `frontend/src/components/ActionBar.jsx`
- [x] Прокинуть `scenario` и `scenario_options` в `consumeScriptStream()` payload
- [x] Обновить `frontend/src/api/client.js` (`generateScript`, `consumeScriptStream`, при необходимости batch)
- [x] Smoke test: минимум 2 сценария на 1 документе

### Фаза B — Справочник сценариев + улучшение UX

Цель: убрать хардкод сценариев из UI и подготовить сценарные опции.

- [x] Добавить backend-справочник сценариев (`SCENARIO_REGISTRY` метаданные)
- [x] Добавить endpoint `GET /settings/scenarios` в `backend/app/routers/api.py`
- [x] Добавить CRUD пользовательских сценариев (`PUT/DELETE /settings/scenarios*`) с сохранением
- [x] Вернуть для сценария: `id`, `name`, `description`, `min_roles`, `max_roles`, `default_roles`, `supported_options`
- [x] Подгружать список сценариев в `ActionBar.jsx` с backend
- [x] Показать описание сценария и подсказку по ролям в UI
- [x] Добавить UI для базовых `scenario_options` (минимум `debate`, `news_digest`)
- [x] Исправить отображение произвольных ролей в `frontend/src/components/ScriptPanel.jsx` (динамические цвета, не только `host/guest1/guest2`)
- [ ] Проверить пакетную генерацию с выбранным сценарием (если UX этого требует)

### Фаза C — Поддержка `роль = модель`

Цель: дать возможность задавать разные модели/эндпоинты для ролей (host/guest/etc.).

- [x] Спроектировать контракт `role_llm_map` (payload/API)
- [x] Добавить поля в backend models (`backend/app/models.py`) с backward compatibility
- [x] Расширить `llm_service` override-конфигом модели/эндпоинта на один вызов
- [x] Добавить валидацию `role_llm_map` (неизвестная роль, пустая модель, недоступный endpoint)
- [x] Добавить UI-настройки LLM по ролям (отдельно от TTS voice) в `frontend/src/components/SettingsPanel.jsx`
- [x] Прокинуть `role_llm_map` в генерацию скрипта
- [x] Добавить понятные ошибки по ролям (например, “модель для guest2 недоступна”)
- [x] Smoke test: 2 роли на разных моделях

### Фаза D — Пошаговая генерация (`turn-taking`)

Цель: спикеры реально реагируют друг на друга по истории реплик.

- [x] Добавить `generation_mode` (`single_pass` / `turn_taking`) в payload/API
- [x] Реализовать цикл генерации реплик в `podcast_service.py`
- [x] На каждом шаге передавать историю реплик + инструкцию “теперь отвечает роль X”
- [x] Поддержать одну модель и разные модели по ролям в одном механизме
- [x] Реализовать стратегии порядка ролей по сценариям (интервью, дебаты, круглый стол и т.д.)
- [x] Добавить лимиты: `max_turns`, target words/minutes, stop conditions
- [x] Нормализовать/валидировать выход реплик на каждом шаге
- [x] Сохранить TTS rewrite как финальный пост-процесс
- [ ] Проверить стабильность JSON-результата и длины скрипта
- [x] (Опционально) Улучшить стриминг прогресса для long-running generation

## Детализация payload (план)

### Этап 1 (MVP)

```json
{
  "minutes": 5,
  "style": "conversational",
  "voices": ["host", "guest1", "guest2"],
  "tts_friendly": true,
  "scenario": "classic_overview",
  "scenario_options": {}
}
```

### Этап 3+ (расширенный)

```json
{
  "minutes": 5,
  "style": "conversational",
  "voices": ["host", "guest1", "guest2"],
  "tts_friendly": true,
  "scenario": "debate",
  "scenario_options": {
    "stance_a": "скептик",
    "stance_b": "оптимист"
  },
  "role_llm_map": {
    "host": { "model": "gemma-3-4b", "base_url": "http://localhost:1234/v1" },
    "guest1": { "model": "qwen-2.5-7b", "base_url": "http://localhost:1234/v1" },
    "guest2": { "model": "llama-3.1-8b", "base_url": "http://localhost:1234/v1" }
  },
  "generation_mode": "turn_taking"
}
```

## Критерии готовности

### MVP (Фаза A)

- [x] Пользователь выбирает сценарий в UI
- [ ] Backend генерирует разные структуры под `classic/interview/debate/news_digest`
- [x] Старые запросы без `scenario` продолжают работать
- [ ] TTS pipeline не ломается

### `роль = модель` (Фаза C)

- [x] Для роли можно задать модель/endpoint
- [x] Ошибки конфигурации показываются с указанием роли
- [x] Выход сохраняет корректные `voice` labels

### `turn-taking` (Фаза D)

- [x] Роли реагируют друг на друга по истории
- [x] Выполняются лимиты длины/длительности
- [ ] Результат стабильно парсится в `[{voice,text}]`

## Журнал выполнения

Заполняем по мере работы (дата, что сделано, что осталось).

- [x] Инициализировать журнал первой записью после начала реализации
- [x] 2026-02-24: Добавлены `scenario` + `scenario_options` в backend/API/frontend payload, сценарный реестр и prompt-guidance в `podcast_service`, селектор сценария и базовые опции (`debate`, `news_digest`) в `ActionBar`. Проверено: `py_compile` backend-файлов и `vite build` frontend. Осталось: smoke/e2e тесты и доведение prompt builder до отдельных template-функций (если сохраняем это как отдельный шаг).
- [x] 2026-02-24: Добавлен backend-каталог сценариев и endpoint `/api/settings/scenarios`, `ActionBar` переведён на загрузку сценариев с fallback, показаны подсказки/рекомендуемые роли, `ScriptPanel` получил динамические цвета для произвольных ролей. Проверено: `py_compile` backend и `vite build` frontend.
- [x] 2026-02-24: Выполнен smoke test backend для `interview` и `debate` (с моками `rag_service`/`llm_service`, временный `CONFIG_YAML`), проверено прохождение `scenario`/`scenario_options` и формирование prompt-guidance. Дополнительно исправлен YAML-синтаксис в `backend/config.yaml` (строка с `instruction` для `educational`).
- [x] 2026-02-24: Пакетная генерация (`DocumentList -> /batch/run`) теперь использует текущие параметры сценария из `ActionBar` через состояние в `App` (`minutes/style/scenario/scenario_options/tts_friendly`) вместо жёстких дефолтов. Проверено: `vite build` frontend.
- [x] 2026-02-24: Добавлен backend groundwork для `role_llm_map`: поле в `PodcastScriptRequest`, `model/base_url` override в `llm_service`, прокидка через обычную/stream/batch генерацию скрипта. В single-pass пока используется один primary override (приоритет `host`, затем порядок ролей). Проверено: `py_compile`, `vite build`, smoke test выбора override.
- [x] 2026-02-24: Добавлена базовая backend-валидация `role_llm_map` (неизвестная роль, пустая `model`) и role-specific сообщения ошибок для обычной/stream генерации. Проверено: `py_compile` + smoke test `validate_role_llm_map`. Недоступность endpoint/модели пока не валидируется заранее.
- [x] 2026-02-24: Добавлена UI-секция `LLM по ролям` в `SettingsPanel` (модель + optional endpoint на роль), состояние `roleLlmMap` поднято в `App` и прокинуто в одиночную/пакетную генерацию через `ActionBar` и `DocumentList`. Проверено: `vite build` frontend.
- [x] 2026-02-24: Добавлен backend preflight для primary role в single-pass (`host` приоритет): проверка endpoint и наличия модели через `/models` с role-specific ошибками (например, для роли `host`). Проверено: `py_compile` и smoke test c моками `list_models`. Ограничение: в single-pass проверяется только выбранный primary override.
- [x] 2026-02-24: Реализован прототип `turn-taking`: `generation_mode` в payload/UI, цикл генерации по ролям с историей реплик и базовыми стратегиями порядка ролей (`interview`/`debate`/`news_digest`/round-robin), per-role LLM override в каждом ходе, лимиты по `max_turns`/минутам, нормализация текста хода и line-by-line stream chunks. Проверено: `py_compile`, `vite build`, smoke test `generation_mode=turn_taking` с 2 ролями и разными моделями.
- [x] 2026-02-24: Добавлено сохранение `LLM по ролям` в backend (`/api/settings/role_llm`, persist в `config.yaml`) и загрузка в `App`/`SettingsPanel`, чтобы role-LLM конфиг сохранялся между сессиями. Проверено: `py_compile`, `vite build`, smoke test `update/get_role_llm_overrides` на временном `CONFIG_YAML`.
- [x] 2026-02-24: Добавлены пользовательские сценарии: backend CRUD (`GET/PUT/DELETE /api/settings/scenarios*`, persist в `data/script_scenarios.json`) и UI-кнопки в `ActionBar` для сохранения/удаления текущего сценария. Проверено: `py_compile`, `vite build`, smoke test `upsert/delete_script_scenario` на временной `DATA_DIR`.
- [x] 2026-02-24: Улучшено редактирование пользовательских сценариев в `ActionBar` (обновление текущего custom-сценария, `min/max` ролей, подстановка полей из выбранного сценария). Исправлен баг: custom-сценарий не влиял на ветку `tts_friendly=false` (fallback `prompt_en <- prompt_ru`). Проверено: `vite build`, `py_compile`, smoke test использования custom-сценария в генерации.

## Ближайший инкремент (рекомендуемый старт)

- [x] Добавить `scenario` + `scenario_options` в backend model и API
- [x] Реализовать `SCENARIO_REGISTRY` и prompt templates (одна модель)
- [x] Добавить selector сценария в `ActionBar`
- [x] Сделать минимум 4 сценария в MVP (`classic`, `interview`, `debate`, `news_digest`)
- [ ] Проверить end-to-end: script -> TTS -> audio
