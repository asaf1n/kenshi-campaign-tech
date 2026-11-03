# llm-payload-optimization: Оптимизация объема и структуры выпуска published/ для LLM

Статус плана: черновик  
Репозиторий: `kenshi-campaign-tech`  
Дата обновления: 2026-11-03

## Цель плана
Оптимизировать артефакты выпуска `published/` технического конвертера `kenshi-campaign-tech` для эффективного потребления моделями в чат-интерфейсах (ChatGPT, Gemini, Claude) и автономными агентами (Codex, Claude Code, Antigravity).
Устранить критические блокеры:
1. Выяснить и устранить причины падения 4 тестов в `unittest` и аномалию 14 vs 30 бойцов (Этап 00).
2. Сократить физический объем скриншотов в публикации с сохранением полного набора в локальном архиве вне git и разметкой keyframes (Этап 01).
3. Подавить генерацию пустых OCR-артефактов при сохранении обязательного `index.json` с эпизодами (Этап 02).
4. Очистить `current.json` от дублирующих `items[].raw` (с предварительной нормализацией `ownedbyTYPE`), скрыть `hidden_stats_raw` и дефолтные навыки строго `== 1.0` (−22%), внедрить шардирование и сборку легковесного `chat-pack/` (только L0 без канона) (Этап 03).
5. Вынести тяжелый RLE-растр и исходные хеши из публикуемого `world_reference.json` с сохранением полного кэша в `data/world` (Этап 04).
6. Очистить технические sha256-заголовки из Markdown-сводок и настроить валидатор на передачу структурированных предупреждений (`validation.warnings`) со статусом `passed` (Этап 05).
7. Обеспечить накопительную дозапись `timeline.jsonl` на основе `changes.json` и фиксацию `branch_switch` при откатах/смене веток (Этап 06).
8. Подготовить для игрока взвешенные варианты оптимизации накопленной git-истории `Kenshi Compain` (Этап 07).

## Инварианты и архитектурные границы
1. **Строгий read-only для оригинальных сейвов и скриншотов**: входной каталог `_screens` и файлы сохранений игры не изменяются.
2. **Детерминированность вывода**: повторный прогон конвейера на тех же входных данных не приводит к искажению числовых показателей отряда.
3. **Обратная совместимость контрактов**: обязательные поля манифеста `kenshi-publication/1` и схемы `contracts/` сохраняются. Опциональные поля и предупреждения (`validation.warnings`) вводятся без нарушения существующих клиентов.
4. **Служебные данные за кадром**: читаемые человеком и LLM документы (`brief.md`, `squad.md`) не содержат сырых sha256-хэшей в заголовках.
5. **Синхронность документации**: при расширении схем и публикации синхронно обновляются `docs/data-contract.md`, `docs/architecture.md`, `AGENTS.md` и `README.md`.
6. **Обязательные проверки**: каждый этап валидируется локальными unit-тестами, `python tests/check_seed.py` и полным прогоном `python tests/run_evidence.py` без немотивированных пропусков.

## Очередь задач

| Задача | Состояние очереди | Назначение |
|---|---|---|
| [00-investigation-and-test-hygiene](00-investigation-and-test-hygiene.md) | открыта (предусловие) | Расследование 4 падений unit-тестов, устранение немотивированных skipped в evidence, разбор аномалии сейва 14 vs 30 бойцов |
| [01-screenshot-optimization-and-clustering](01-screenshot-optimization-and-clustering.md) | в очереди | Даунскейл до 1600 px q80, кластеризация `dt <= 180s`, keyframes в публикацию, полный архив локально вне git, тест сжатия на фикстуре |
| [02-ocr-suppression-and-cleanup](02-ocr-suppression-and-cleanup.md) | в очереди | Подавление пустых OCR (`feed.md`, `index.md`, `annotations.jsonl`), обязательный `index.json`, опциональность в схеме манифеста |
| [03-release-sharding-and-payload-reduction](03-release-sharding-and-payload-reduction.md) | в очереди | Очистка `current.json`, нормализация `ownedbyTYPE` до удаления raw, навыки 1.0, шардирование L0/L1/L2, экспорт L0 chat-pack ($\le$ 40k токенов) |
| [04-world-reference-slimming](04-world-reference-slimming.md) | в очереди | Публикационная проекция `world_reference.json` без `regions_map` и `source_hashes` (<220k символов), сохранение полного кэша в `data/world` |
| [05-validation-and-headers](05-validation-and-headers.md) | в очереди | Очистка sha256 в `brief.md`/`squad.md`, передача `validation.warnings` со статусом `passed`, сквозная доставка |
| [06-cumulative-timeline-and-branch-switch](06-cumulative-timeline-and-branch-switch.md) | в очереди | Накопительный `timeline.jsonl` по событиям из `changes.json`, детектор смены ветки `branch_switch` |
| [07-git-history-options](07-git-history-options.md) | в очереди | Подготовка вариантов оптимизации накопленной истории git для решения игрока (без автоматических деструктивных правок) |
