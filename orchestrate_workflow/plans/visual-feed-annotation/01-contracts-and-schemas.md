# Задача 01: Контракты и схемы разметки скриншотов

## Цель
Разработать спецификацию и строгую схему JSON Schema для машиночитаемой разметки скриншотов `kenshi-screenshot-annotation/1`, создать эталонный пример в `examples/`, внедрить валидацию в `tools/validation.py` и поддержать регистрацию артефактов `screenshot_annotations` и `screenshot_feed` в `manifest.schema.json`.

## Требования к схеме
1. Формат: `kenshi-screenshot-annotation/1`.
2. Обязательные поля строки аннотации:
   - `format`: const `"kenshi-screenshot-annotation/1"`
   - `sha256`: pattern `^[0-9a-f]{64}$`
   - `filename`: имя файла скриншота
   - `order`: порядковый номер в 7-дневном окне
   - `modified_at`: ISO 8601
   - `meta`:
     - `game_day`: int | null
     - `game_time`: string | null (формат HH:MM)
     - `scene_group_id`: string (идентификатор эпизода/сцены)
     - `is_exact_duplicate`: bool
     - `duplicate_of`: string | null (SHA-256 оригинала при дубле)
   - `ui_text`:
     - `location`: string | null
     - `characters`: list[string]
     - `dialogues`: list[object: {speaker: string|null, text: string, confidence: number}]
     - `notifications`: list[string]
     - `items_or_tasks`: list[string]
   - `visual`:
     - `scene_type`: enum [travel, combat, dialogue, trade, building, exploration, base, inventory, other]
     - `present_characters`: list[string]
     - `environment`: string | null
     - `actions`: list[string]
     - `summary`: string
   - `provenance`:
     - `source`: enum [gemini-2.5-flash, gemini-2.5-flash-lite, ocr, heuristic, cache]
     - `confidence`: number (0.0 .. 1.0)
     - `annotated_at`: ISO 8601
3. Обновление `contracts/manifest.schema.json`:
   - Добавление опциональных артефактов:
     - `screenshot_annotations`: `"screenshots/annotations.jsonl"`
     - `screenshot_feed`: `"screenshots/feed.md"`
4. Валидация в `tools/validation.py`:
   - Проверка схемы для каждой строки `annotations.jsonl`.
   - Проверка связности по SHA-256 с зарегистрированными файлами скриншотов.
   - Проверка отсутствия персональных путей и утечек приватных данных.
