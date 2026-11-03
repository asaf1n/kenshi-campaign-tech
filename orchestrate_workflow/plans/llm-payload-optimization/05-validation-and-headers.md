# 05-validation-and-headers: Очистка заголовков Markdown и предупреждения валидатора

## Цель
Устранить попадание технических sha256-хэшей в ответы игрового компаньона, заменив заголовки в `brief.md` и `squad.md` на понятные названия по игровому времени (`# Краткая сводка: День X, ЧЧ:ММ`), а также настроить передачу структурированных предупреждений валидатора (`validation.warnings` со строгим значением `artifact:timeline: empty` при пустом `timeline.jsonl`), обеспечив сквозную совместимость с доставкой при сохранении статуса `passed`.

## Решения
- 2026-11-03. Наличие сырых `snapshot_id` в заголовках первого уровня приводит к их прямому копированию языковыми моделями. Заголовки формируются строго по игровому времени: `# Краткая сводка: {day_str}{time_str}` и `# Состав отряда: {day_str}{time_str}` (при отсутствии времени — fallback на `# Краткая сводка: Срез без даты`).
- 2026-11-03. Для сохранения обратной совместимости с `contracts/manifest.schema.json` и `deliver_companion.py`:
  - `validation.status` сохраняет значение `"passed"`, если все обязательные проверки успешны.
  - В объект `validation` схемы добавляется опциональный массив `warnings` (`type: array, items: {type: string}`).
  - При обнаружении пустого файла `timeline.jsonl` формируется строго фиксированное предупреждение: `"artifact:timeline: empty"`.
  - Публикатор (`tools/publisher.py`) актуализирует записанный `manifest.json` списком предупреждений от `validate_publication`.
  - Утилита доставки `deliver_companion.py` допускает доставку при `status == "passed"`, выводя предупреждения в консоль.
- 2026-11-03. Синхронно обновляются `docs/data-contract.md` и `docs/acceptance.md`.

## Этапы задачи

1. **Очистка рендеринга заголовков (`tools/rendering.py`)**. Заменить вывод сырых `snapshot_id` в заголовках первого уровня на форматированное игровое время: `# Краткая сводка: {day_str}{time_str}` и `# Состав отряда: {day_str}{time_str}`.
2. **Схема и логика предупреждений валидатора (`contracts/manifest.schema.json`, `tools/validation.py`, `tools/publisher.py`, `docs/data-contract.md`)**. Добавить поле `warnings` в схему манифеста. В `validate_publication` при нулевом размере `timeline.jsonl` формировать предупреждение `"artifact:timeline: empty"`. Передавать предупреждения в финальный `manifest.json`.
3. **Обновление утилиты доставки (`tools/deliver_companion.py`)**. Обеспечить вывод `validation.warnings` при доставке обновленного бандла в `Kenshi Compain`.
4. **Тесты и сквозная верификация (`tests/test_publish.py`, `tests/test_validation.py`, `tests/run_evidence.py`)**. Проверить рендеринг заголовков, валидацию и доставку.
