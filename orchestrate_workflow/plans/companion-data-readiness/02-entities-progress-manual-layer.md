# 02-entities-progress-manual-layer

## Цель

Создать надежный архитектурный механизм передачи и подтверждения персональных свидетельств побед над боссами и захвата трофейного оружия Мейто через ручной слой `Campaign/<campaign_id>/entities_progress.json` в репозитории `Kenshi-Compain`, обеспечивающий автоматическое обогащение `unique_entities_registry.json` и генерацию плоской аналитической витрины `published/tables/unique_entities.csv` при доставке конвейером без вмешательства в локальный таймлайн.

## Решения

- 2026-11-12. Корректировка по ревью: спроектировать спецификацию и схему ручного слоя `contracts/entities-progress.schema.json` (формат `kenshi-entities-progress/1`) по единому образцу с `locations_progress.json`: словарь вида `{sid: {"outcome": ..., "evidence": ..., "game_time": ...?}}` без `event_id` (он уникален для локаций, у боссов и Мейто его нет). Для боссов `sid = base_sid` (или персональный `sid`, если задан), для оружия — `base_sid` записи Мейто.
- 2026-11-12. Корректировка по ревью: исключить разбор маркеров из таймлайна (`[npc_sid=...]`, `[base_sid=...]`) — в реальной кампании летописец их не ставит (в текущем выпуске они дали 0 совпадений). Таймлайн остаётся техническим скользящим окном; персональные свидетельства и исходы задаются исключительно через `entities_progress.json`.
- 2026-11-12. Корректировка по ревью: в `tools/unique_entities_registry.py` реализовать функцию `apply_progress_to_unique_entities`:
  1) при наличии валидного `entities_progress` сопоставлять боссов по `base_sid` / `sid` и обогащать их объективными полями `outcome`, `evidence` (в `personal_evidence`), обновляя статус наблюдения/зачистки;
  2) сопоставлять оружие Мейто по `base_sid` и проставлять `provenance_status = "confirmed_trophy"`, а в `trophy_evidence` переносить данные трофея;
  3) сохранять полную чистоту функции (deep copy без мутации исходных структур) и детерминизм вывода.
- 2026-11-12. Корректировка по ревью: в `tools/deliver_companion.py` добавить чтение ручного слоя `Campaign/<campaign_id>/entities_progress.json` из репозитория компаньона, его валидацию по JSON-схеме и передачу в `apply_progress_to_unique_entities`. При синтаксической ошибке или нарушении схемы фиксировать `entities_progress_status = "invalid"`, логировать предупреждение и продолжать доставку с базовыми данными без срыва конвейера (по аналогии с обработкой `locations_progress.json`).
- 2026-11-12. Корректировка по ревью: реализовать вывод уникальных сущностей в плоскую аналитическую витрину `published/tables/unique_entities.csv` в `tools/tables.py` (`unique_entities_table`): категория (`boss`, `meitou`), имя, локализованное имя `name_ru`, статус наблюдения, статус принадлежности (`provenance_status`), признак зачистки / победы, исход (`outcome`), свидетельство (`evidence`).

## Этапы задачи

1. Разработка схемы `contracts/entities-progress.schema.json` (формат `kenshi-entities-progress/1`) по образцу `location-progress` ({sid: {outcome, evidence, game_time?}}) и валидатора в `tools/validation.py`. Готово: схема создана и валидирует эталонный JSON; функция `validate_entities_progress` проверяет контракт и изолирует невалидные форматы; unit-тесты валидации проходят. Не начат.
2. Реализация слияния ручного слоя в `tools/unique_entities_registry.py`: обогащение `personal_evidence` боссов и `provenance_status`/`trophy_evidence` оружия Мейто без разбора маркеров таймлайна. Готово: unit-тесты подтверждают корректное выставление свидетельств и подтверждение трофеев Мейто при передаче слоя `entities_progress`; при отсутствии слоя артефакты сохраняют дефолтные значения. Не начат.
3. Интеграция чтения и валидации `entities_progress.json` в `tools/deliver_companion.py`. Готово: доставка читает файл из целевого репозитория компаньона, валидирует схему, изолирует сбои при `invalid` и перезаписывает `unique_entities_registry.json` обогащенными данными; тесты доставки проходят. Не начат.
4. Создание плоской аналитической витрины `published/tables/unique_entities.csv` в `tools/tables.py` и регрессионное тестирование. Готово: в `tables/unique_entities.csv` выводятся категория, статусы наблюдения и принадлежности, зачистка, исход и свидетельства; `python -m tools.campaign check-schemas` и unit-тесты завершаются успешно. Не начат.
