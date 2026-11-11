# unique-entities-registry

Статус плана: утверждён 2026-11-11

Цель плана: Расширить конвертер `kenshi-campaign-tech` техническим реестром уникальных NPC, боссов и оружия Мейто (`published/unique_entities_registry.json`, схема `kenshi-unique-entities-registry/1`) с детерминированной привязкой к игровым локациям (`instance_id`), наблюдаемому состоянию кампании (снимки `current.json`, отряд `characters`, `world_state`) и подтверждённым фактам (`locations_progress.json`), строго сохраняя независимость фактов от игровых решений и не допуская автоматических отметок `cleared`.

Открытая задача: [03-pipeline-publication-delivery-and-tables](03-pipeline-publication-delivery-and-tables.md)

## Очередь задач

| Задача | Состояние очереди | Назначение |
|---|---|---|
| [01-static-graph-and-extraction](01-static-graph-and-extraction.md) | завершена | Извлечение статического графа `CHARACTER -> SQUAD_TEMPLATE -> TOWN (residents, bar squads) -> instance_id` в `gamedb.py`, сохранение `template_sid` персонажей в `converter_worker.py` и связывание по `handle` |
| [02-unique-entities-registry-model-and-schema](02-unique-entities-registry-model-and-schema.md) | завершена | Схема `contracts/unique-entities-registry.schema.json` (/1), модель данных, выделение боссов, учет Мейто, обработка неоднозначностей 1:N и модуль сборки реестра |
| [03-pipeline-publication-delivery-and-tables](03-pipeline-publication-delivery-and-tables.md) | открыта | Интеграция в `manifest.schema.json`, `publisher.py`, `validation.py`, `deliver_companion.py`, проверка идемпотентности `generated_at` и табличные выгрузки |
| [04-testing-and-verification](04-testing-and-verification.md) | ожидает | Модульные, интеграционные и регрессионные тесты по всем сценариям и контрольным точкам (Башня Ожога, Столица людоедов, Башня Жестокости, Мейто, ложные зачистки) |
