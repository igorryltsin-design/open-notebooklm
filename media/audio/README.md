# Локальные аудио-ассеты

Сюда складываются большие локальные музыкальные файлы для intro / background / outro.

Принцип:

- в git коммитится только эта README;
- сами `.mp3`/`.wav` файлы игнорируются;
- при сборке backend Dockerfile копирует содержимое `media/audio/` в `/opt/audio-assets` внутри контейнера.

Если в каталоге есть `zvuk-sovershennogo-volshebstva-1149.mp3`, он используется как локальный override для `intro/background/outro`.
