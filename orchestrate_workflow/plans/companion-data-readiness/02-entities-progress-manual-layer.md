# 02-entities-progress-manual-layer

## Цель

Создать надежный архитектурный механизм передачи и подтверждения персональных свидетельств побед над боссами и захвата трофейного оружия Мейто через ручной слой `Campaign/<campaign_id>/entities_progress.json` в репозитории `Kenshi-Compain`, обеспечивающий автоматическое обогащение `unique_entities_registry.json` и `unique_entities.csv` при доставке конвейером без вмешательства в локальный таймлайн.

## Решения

- 2026-11-12. Аудит audit-2026-11.md (Находка 2): спроектировать спецификацию и схему ручного слоя `contracts/entities-progress.schema.json` (формат `kenshi-entities-progress/1`), содержащего разделы `npcs` (sid -> статус `defeated`/`captured`/`dead`/`released`, `game_day`, `event_id`, `summary`, `evidence`) и `trophies` (sid -> статус `captured`/`owned`/`lost`, `game_day`, `event_id`, `summary`, `evidence`).
- 2026-11-12. Аудит audit-2026-11.md (Находка 2): в `tools/unique_entities_registry.py` расширить функцию `apply_progress_to_unique_entities`:
  1) при наличии валидного `entities_progress` сопоставлять боссов по `npc_sid` и проставлять `personal_evidence` с данными события;
  2) сопоставлять оружие Мейто по `base_sid` и проставлять `provenance_status = "confirmed_trophy"`, а в `trophy_evidence` переносить метаданные трофея;
  3) сохранять полную чистоту функции (deep copy без мутации исходных структур) и детерминизм вывода.
- 2026-11-12. Аудит audit-2026-11.md (Находка 2): в `tools/deliver_companion.py` добавить чтение ручного слоя `Campaign/<campaign_id>/entities_progress.json` из репозитория компаньона, его валидацию по JSON-схеме и передачу в `apply_progress_to_unique_entities`. При синтаксической ошибке или нарушении схемы фиксировать `entities_progress_status = "invalid"`, логировать ошибку и продолжать доставку с базовыми данными без срыва конвейера (по аналогии с обработкой `locations_progress.json`).
- 2026-11-12. Аудит audit-2026-11.md (Находка 2): синхронизировать генерацию плоской витрины `tables/unique_entities.csv` в `tools/tables.py` (`unique_entities_table`), отражая обновленные статусы `personal_evidence` и подтвержденные трофеи Мейто.

## Этапы задачи

1. Разработка схемы `contracts/entities-progress.schema.json` и валидатора в `tools/validation.py`. Готово: схема создана и валидирует эталонный JSON; функция `validate_entities_progress` проверяет контракт и обрабатывает невалидные форматы; unit-тесты валидации проходят. Не начат.
2. Реализация слияния ручного слоя в `tools/unique_entities_registry.py`: обогащение `personal_evidence` боссов и `provenance_status`/`trophy_evidence` оружия Мейто. Готово: unit-тесты подтверждают корректное выставление `personal_evidence` и подтверждение трофеев Мейто при передаче слоя `entities_progress`; при отсутствии слоя артефакты сохраняют дефолтные значения. Не начат.
3. Интеграция чтения и валидации `entities_progress.json` в `tools/deliver_companion.py`. Готово: доставка читает файл из целевого репозитория компаньона, валидирует схему, изолирует сбои при `invalid` и перезаписывает `unique_entities_registry.json` и `unique_entities.csv` обогащенными данными; тесты доставки проходят. Не начат.
4. Обновление плоской таблицы `tables/unique_entities.csv` в `tools/tables.py` и регрессионное тестирование. Готово: в `unique_entities.csv` появляются статусы подтверждения боссов и трофеев; `python -m tools.campaign check-schemas` и unit-тесты завершаются успешно. Не начат.
