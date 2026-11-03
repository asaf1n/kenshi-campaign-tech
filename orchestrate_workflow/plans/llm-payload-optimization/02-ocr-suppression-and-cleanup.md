# 02-ocr-suppression-and-cleanup: Подавление пустых OCR-артефактов

## Цель
Устранить неоправданный расход контекста LLM (~235 тыс. токенов) на пустые OCR-выгрузки. При отсутствии содержательного текста интерфейса не публиковать `feed.md`, `index.md` и `annotations.jsonl`. Структурированный `index.json` со списком кадров и keyframes публикуется ВСЕГДА. В манифесте артефакты OCR помечаются как опциональные, а утилита доставки очищает устаревшие файлы строго внутри целевого каталога скриншотов.

## Решения
- 2026-11-03. В текущем релизе OCR распознал 0 из 343 кадров (`provenance.source = metadata`), породив 4 избыточных представления почти без смысловой информации.
- 2026-11-03. `index.json` со списком кадров, временными эпизодами и keyframes публикуется ВСЕГДА как обязательный машиночитаемый индекс скриншотов выпуска.
- 2026-11-03. Содержательность OCR оценивается по полному набору полей контракта `ocr` (`tools/screenshot_extractor.py:207`): `dialogues`, `characters`, `location`, `game_day`, `game_time`, `notifications`, `items_or_tasks`. Если во всех кадрах выпуска каждое из этих полей пусто (`None`, `[]`, `""`), выпуск считается не содержащим распознанного текста. Наличие даже изолированного распознанного дня (`game_day`) или времени (`game_time`) считается содержательным результатом и сохраняет публикацию ленты.
- 2026-11-03. При полностью пустом OCR публикатор подавляет генерацию `feed.md`, `index.md` и `annotations.jsonl`. В `contracts/manifest.schema.json` и `manifest.json["artifacts"]` ключи `screenshot_annotations` и `screenshot_feed` являются опциональными.
- 2026-11-03. Утилита доставки `deliver_companion.py` дорабатывается: при синхронизации каталога `screenshots/` удаляются файлы, отсутствующие в текущей публикации (`annotations.jsonl`, `feed.md`, `index.md`), строго внутри `published/screenshots/`. Любые каталоги за пределами `published/screenshots/` (включая `Campaign/`) защищены от удаления.
- 2026-11-03. Синхронно обновляются `docs/data-contract.md`, `docs/architecture.md` и `docs/chatgpt-interface.md:21`.

## Этапы задачи

1. **Критерий содержательности OCR и опциональность в схеме (`tools/publisher.py`, `contracts/manifest.schema.json`, `docs/data-contract.md`)**. Реализовать проверку всех полей `ocr`. При нулевом содержательном выходе исключать `feed.md`, `index.md`, `annotations.jsonl` из публикации и из `manifest.json["artifacts"]`. Отразить опциональность в схеме манифеста. `index.json` публиковать всегда.
2. **Очистка устаревших артефактов при доставке (`tools/deliver_companion.py`)**. Добавить в `deliver_companion.py` удаление из целевого каталога `published/screenshots/` файлов, отсутствующих в исходной публикации (`annotations.jsonl`, `feed.md`, `index.md`), с жестким ограничением областью `published/screenshots/`.
3. **Регрессионные тесты и валидация (`tests/test_screenshot_annotations.py`, `tests/test_screenshots.py`, `tests/test_publish.py`)**. Покрыть сценарий подавления пустых артефактов тестами (включая проверку сохранения разметки при наличии только `game_day`/`game_time`), проверить валидацию манифеста при отсутствии опциональных OCR-файлов.
4. **Синхронизация документации**. Обновить `docs/chatgpt-interface.md:21` и `docs/data-contract.md`.
