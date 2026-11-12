# companion-data-readiness

Статус плана: утверждён 2026-11-12

Цель плана: Довести опубликованные артефакты published/ и конвейер доставки до полной готовности под задачи игрового компаньона Kenshi (выбор направлений походов с учётом Stronger Opponent Logic — модель сама оценивает сопротивление и потенциал прокачки по объективным характеристикам отрядов, лидеру и снаряжению; исследование лута руин; персональные подтверждения боссов и Мейто через ручной слой; компактный объём контекста для GitHub-коннектора Custom GPT; локализация и история прокачки отряда) в соответствии с находками аудита и корректировкой после ревью.

Архитектурное решение по Находке 9 (монолит tools/tables.py и парсеры): Декомпозиция новых витрин выносится в отдельные специализированные модули (`tools/skills_history.py`, `tools/encounters.py`, `tools/ruins_loot.py`), исключая раздувание исторического монолита `tools/tables.py` (в нём остаётся только минимальная регистрация внешних генераторов). Глубокий рефакторинг исторического монолита `tables.py` (деление на `tables_squad.py`, `tables_world.py`, `tables_gamedata.py`) и связки `session_export` + `save_reader` мотивированно отложен в отдельный техдолг-план, чтобы не рисковать стабильностью базового парсера форматов v10/v11 при создании новой аналитической функциональности компаньона.

Открытая задача: [03-published-payload-optimization](03-published-payload-optimization.md)

| Задача | Состояние очереди | Назначение |
|---|---|---|
| [01-localization-and-meta-cleanup](01-localization-and-meta-cleanup.md) | закрыта | Подстановка русских имен в location_registry.json, очистка town_overrides.csv, weapon_name_ru для Мейто в unique_entities_registry, вычистка остатков мета-текста в rendering.py, diff.py, tables.py, feed.md (Находки 4 и 8). Завершена 2026-11-12 |
| [02-entities-progress-manual-layer](02-entities-progress-manual-layer.md) | закрыта | Ручной слой entities_progress.json в Kenshi-Compain, чтение, валидация по схеме и объединение в deliver_companion.py и unique_entities_registry.py, обогащение personal_evidence боссов и подтверждения трофеев Мейто (Находка 2). Завершена 2026-11-12 |
| [03-published-payload-optimization](03-published-payload-optimization.md) | открыта | Сжатие brief.md до 3-5 КБ, облегчение squad.md, устранение дублирования с CSV, компактный tables/index.md, исключение ссылок на тяжелые JSON из brief.md и адресация к компактным витринам без переноса файлов (Находка 3) |
| [04-skills-history-and-squad-dynamics](04-skills-history-and-squad-dynamics.md) | ожидает | Историческая витрина skills_history.csv в выделенном модуле tools/skills_history.py по цепочке снимков кампании, динамика характеристик, казны и состава отряда во времени (Находка 5, архитектурное решение по Находке 9) |
| [05-region-encounters-and-ruins-loot](05-region-encounters-and-ruins-loot.md) | ожидает | Сводная плоская витрина region_encounters.csv в tools/encounters.py; парсинг статического лута руин в tools/ruins_loot.py для world_ruins_loot.csv без раздувания tables.py (Находка 1, архитектурное решение по Находке 9) |
| [06-contract-tests-and-e2e-verification](06-contract-tests-and-e2e-verification.md) | ожидает | Синхронизация docs/data-contract.md по state_code/location_resolution, калибровка 64 предметов unverified_class, стабилизация 4 LIVE-SLOT тестов, прогон unit-тестов, схем и сквозного конвейера Initialize-Pipeline.ps1 (Находки 6, 7, 10) |
