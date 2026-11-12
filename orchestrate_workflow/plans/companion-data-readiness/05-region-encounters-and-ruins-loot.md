# 05-region-encounters-and-ruins-loot

## Цель

Создать ключевые плоские витрины `region_encounters.csv` (на базе модуля `tools/encounters.py`) и `world_ruins_loot.csv` (в выделенном модуле `tools/ruins_loot.py`), устраняющие информационный дефицит компаньона и позволяющие LLM автономно и безопасно планировать маршруты походов отряда без многотабличных соединений и без раздувания исторического монолита `tools/tables.py`.

## Решения

- 2026-11-12. Аудит audit-2026-11.md (Находки 1 и 9, ревью плана): расширить существующий модуль `tools/encounters.py` функцией построения плоской аналитической витрины `published/tables/region_encounters.csv`, избегая переноса этой логики в `tools/tables.py`:
  1) связать `region` (через `place_sid` биом-групп и зоны карты) с данными `squad_spawns`, `squad_templates`, `squad_members`, `unit_templates` и `unit_template_gear`;
  2) поля таблицы: `region_name_ru, region_name_en, place_sid, faction_name, squad_name, spawn_chance_pct, squad_size_range, key_roles, avg_combat_stats, max_weapon_grade, max_armour_grade`;
  3) агрегация боевых характеристик юнитов (атака, защита, сила, стойкость) и максимального качества экипировки (грейд оружия и брони) для мгновенной оценки боевой угрозы региона.
- 2026-11-12. Аудит audit-2026-11.md (Находки 1 и 9, ревью плана): создать выделенный модуль `tools/ruins_loot.py` для парсинга и экстракции статического лута руин из FCS и игровых файлов в `published/tables/world_ruins_loot.csv`:
  1) извлечь из данных модов и базовой игры контейнеры руин, лабораторий и заброшенных библиотек;
  2) выгрузить статические и взвешенные спавны ценного исследовательского лута (AI Cores, Ancient Science Books, Engineering Research, чертежи, редкое снаряжение);
  3) поля таблицы: `location_name_ru, location_name_en, region, ruin_type, container_type, item_name_ru, item_name_en, count_range, spawn_chance_pct`.
- 2026-11-12. Аудит audit-2026-11.md (Находки 1 и 9): в `tools/tables.py` реализовать только вызовы генераторов из `tools/encounters.py` и `tools/ruins_loot.py` и регистрацию результирующих витрин в реестре таблиц; обновить документацию `docs/data-contract.md` и подтвердить детерминизм генерации.

## Этапы задачи

1. Реализация сборки плоской витрины `region_encounters.csv` в `tools/encounters.py`. Готово: модуль `tools/encounters.py` формирует плоскую таблицу, агрегируя данные по отрядам, ролям, усредненным характеристикам и качеству экипировки; unit-тесты в `tests/test_encounters.py` проверяют корректность расчетов. Не начат.
2. Создание выделенного модуля `tools/ruins_loot.py` для экстракции статического лута руин и генерации `world_ruins_loot.csv`. Готово: модуль `tools/ruins_loot.py` извлекает статические предметы и контейнеры руин из справочника игры; таблица `world_ruins_loot.csv` содержит актуальный лут лабораторий и руин; unit-тесты в `tests/test_ruins_loot.py` подтверждают наличие научных книг и ИИ-ядер. Не начат.
3. Регистрация вызовов генераторов в `tools/tables.py`, обновление словаря колонок и `docs/data-contract.md`. Готово: обе витрины включены в `published/tables/` через импорт из специализированных модулей, документированы в контракте; детерминизм подтвержден повторным прогоном. Не начат.
