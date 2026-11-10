# 03-pipeline-publication-delivery-and-tables

## Цель

Интегрировать реестр уникальных сущностей в сквозной конвейер публикации, валидацию, манифест, одностороннюю доставку в `Kenshi-Compain` и табличные выгрузки:
1. `contracts/manifest.schema.json`:
   - Добавить опциональное свойство `"unique_entities_registry": { "type": "string", "minLength": 1 }` в `properties.artifacts.properties`.
2. `tools/publisher.py`:
   - Встроить вызов `build_unique_entities_registry(cur, world_ref, game_records, game_index, progress_data, timeline_data)` в `publish()` под `PublishLock` (используя отфильтрованную цепочку событий `timeline_data`).
   - Записать `staging_dir / "unique_entities_registry.json"`.
   - Зарегистрировать в `manifest["artifacts"]["unique_entities_registry"] = "unique_entities_registry.json"` и `manifest["validation"]["checks"]`.
   - Обеспечить побайтовую идемпотентность `generated_at` при повторной публикации того же снимка.
3. `tools/validation.py`:
   - Добавить `"unique-entities-registry"` в белый список `validate()`.
   - В `validate_publication()` добавить проверку `if "unique_entities_registry" in artifacts: validate(data, "unique-entities-registry")` со сверкой `campaign_id`, `snapshot_id`, `world_ref_id`, `game_time`.
   - Проверить соблюдение приватности (`check_privacy`).
4. `tools/deliver_companion.py`:
   - В `_write_release` добавить копирование `unique_entities_registry.json` в `target_published` при наличии в `source_published` (предотвращая сбой `validate_publication(target_published)`).
   - В `write_release` использовать отфильтрованный по игровому времени ручной слой `progress_data` (из `load_progress` / `merge_progress`) для обогащения отметок зачистки у боссов в `unique_entities_registry.json`.
5. Табличные зеркала:
   - В `tools/tables.py` сформировать отдельную таблицу `published/tables/unique_entities.csv` (единый зафиксированный вариант):
     - Колонки: `entity_kind` (`npc`, `boss`, `meitou`), `sid`, `name_ru`, `name_en`, `state_code`, `squad_status`, `character_id`, `primary_location_instance_id`, `location_resolution`, `meitou_base_sid`, `carrier_or_storage`, `provenance_status`.
   - Обновить `tables/index.md` и документацию `docs/data-contract.md`.

## Решения

- 2026-11-11. Выявлено критическое ограничение `tools/deliver_companion.py`: функция `_write_release` копирует файлы по жёсткому списку `REQUIRED_ARTIFACTS`. Без явного добавления нового файла в `_write_release` доставка упадёт на валидации целевого release bundle.
- 2026-11-11 (по предложению ревьювера). Зафиксирован один вариант табличного зеркала: выгрузка новой таблицы `published/tables/unique_entities.csv` с регистрацией в `tables/index.md` без ломки существующих CSV-таблиц.
- 2026-11-11. `tools/gdrive_sync.py` не требует изменений: JSON-артефакты публикации (`manifest.json`, `location_registry.json`) не входят в профиль Google Docs/Sheets, а наличие `unique_entities_registry.json` автоматически попадает в `_published_candidates` и не вызывает ложных удалений `plan_prune`. Новая таблица `tables/unique_entities.csv` автоматически подхватывается синхронизатором таблиц.

## Этапы задачи

1. Обновление `contracts/manifest.schema.json`, `tools/validation.py` и `tools/publisher.py`. Готово: локальный прогон `python -m tools.campaign publish` атомарно выпускает `published/unique_entities_registry.json`, `validation.status == "passed"`. Не начат.
2. Доработка `tools/deliver_companion.py` и добавление `unique_entities.csv` в `tools/tables.py`. Готово: сухой прогон доставки во временный каталог проходит валидацию, таблица `tables/unique_entities.csv` зарегистрирована в `tables/index.md`. Не начат.
3. Актуализация проектной документации `docs/data-contract.md`, `docs/architecture.md`. Готово: документация отражает структуру нового артефакта, `git diff --check` чист. Не начат.
