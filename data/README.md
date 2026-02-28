# Runtime data

Каталог `data/` — это локальное runtime-хранилище приложения.

Здесь появляются:

- `inputs/` — исходные файлы документов;
- `outputs/` — generated artifacts (preview PDF, audio, video, export bundles);
- `index/` — данные векторного индекса;
- `documents.json`, `projects.json`, `chat_history.json`, `jobs*.json` — runtime-метаданные.

Этот каталог не является частью исходного кода и не должен коммититься в git.
