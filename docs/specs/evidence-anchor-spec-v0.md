# Evidence/Anchor Spec v0

Статус: draft v0  
Дата: 2026-02-27

## Зачем

Текущая подсветка часто опирается на поиск похожей строки. Для устойчивого перехода из чата в источник нужен детерминированный anchor.

## Термины

- `evidence`: нормализованный фрагмент источника, использованный в ответе.
- `anchor`: детерминированный локатор внутри документа.

## Целевая модель (API-уровень)

```json
{
  "evidence_id": "ev:doc123:chunk45:1",
  "document_id": "doc123",
  "chunk_id": "chunk45",
  "chunk_index": 44,
  "score": 0.7132,
  "quote": "короткая цитата/фраза",
  "text_preview": "обрезанный текст для карточки",
  "source_type": "text|ocr|table|figure",
  "anchor": {
    "anchor_id": "a:doc123:p18:o4421:67",
    "kind": "text_offset",
    "page": 18,
    "slide": null,
    "section_path": "Глава 2 / ...",
    "start_offset": 4421,
    "end_offset": 4488,
    "bbox": null
  },
  "highlights": [
    {
      "start_offset": 4421,
      "end_offset": 4488,
      "text": "..."
    }
  ]
}
```

## Минимально обязательные поля

- `evidence_id`
- `document_id`
- `chunk_id`
- `score`
- `anchor.anchor_id`
- `anchor.kind`

## Правила формирования `anchor_id`

- Формат: `a:{document_id}:{locator}`
- Для text-offset: `a:{doc}:p{page}:o{start}:{len}`
- Для slide: `a:{doc}:s{slide}:o{start}:{len}`
- Если offsets нет: временный fallback `a:{doc}:chunk:{chunk_id}` (пониженный приоритет).

## Стратегия подсветки (UI)

1. Если есть offsets: подсветка по offsets (primary path).
2. Если offsets нет: fuzzy-поиск по `quote/text_preview` (fallback).
3. Если fallback не сработал: показать notice "точная привязка недоступна".

## Пошаговая реализация

1. Добавить поля `evidence_id`, `anchor` в backend model.
2. Заполнять `anchor_id` в `rag_service/podcast_service` при сборке citations.
3. Прокинуть в `/chat/query*`, `/voice-qa*`.
4. Обновить `SourceViewer` на приоритет offsets.
5. Добавить e2e-проверку перехода по `anchor_id`.

## Риски

- Старые документы не имеют offsets для каждого фрагмента.
- Разные парсеры (PDF/DOCX/PPTX) дают разный уровень точности.

## Совместимость

- Старый формат citation сохраняется.
- Новый блок `anchor` добавляется как расширение без breaking changes.
