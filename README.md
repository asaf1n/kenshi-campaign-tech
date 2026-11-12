# Kenshi Campaign Tech

Автономный локальный инженерный конвертер для обработки сохранений Kenshi, парсинга скриншотов и сборки подтверждённого release bundle (`published/`) для репозитория игрового компаньона [asaf1n/Kenshi-Compain](https://github.com/asaf1n/Kenshi-Compain).

Художественные хроники, описания кампаний и системные инструкции для чат-ИИ моделей вынесены в игровой репозиторий. Этот репозиторий содержит исключительно код конвертера, парсеров, валидаторов и схем контрактов.

## Быстрый старт

Запуск полного цикла обработки свежего сохранения и односторонней доставки в игровой компаньон выполняется одной командой:

```powershell
.\Initialize-Pipeline.ps1
```

Либо по шагам через CLI:

```powershell
# 1. Извлечение, diff, разрешение координат, рендеринг и публикация в published/:
python -m tools.campaign pipeline --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/autosave0" --campaign-id hot-lon

# 2. Односторонняя доставка release bundle в репозиторий компаньона (с отправкой в origin/main):
python -m tools.campaign deliver-companion --companion-dir "C:\Users\artem\Kenshi Compain" --push
```

## Состав release bundle (`published/`)

Каталог `published/` генерируется конвейером атомарно и содержит подтверждённый срез состояния:

1. `manifest.json` (`kenshi-publication/2`) — машинный паспорт публикации (`snapshot_id`, `parent_snapshot_id`, `world_ref_id`, статус валидации `validation.status == "passed"`).
2. `extended/current.json` (`kenshi-session/11`) — полный нормализованный срез сохранения.
3. `brief.md` и `squad.md` — текстовая сводка и детализированная ведомость отряда для контекста LLM.
4. `changes.json` (`kenshi-session-diff/5`) и `changes.md` — структурный и читаемый diff относительно предка.
5. `locations.json` (`kenshi-locations/1`) — пространственные координаты и группы отряда.
6. `world_reference.json` (`kenshi-world-reference/1`) — статический паспорт локаций и регионов мира.
7. `location_registry.json` (`kenshi-location-registry/3`) — реестр известности и зачистки 261 локации.
8. `unique_entities_registry.json` (`kenshi-unique-entities-registry/2`) — уникальные NPC, боссы и оружие Meitou.
9. `timeline.jsonl` (`kenshi-event/1`) — журнал подтверждённых и автоматических событий цепочки.
10. `tables/` — 24 таблицы CSV и `tables/index.md` (отряд, экипировка, инвентарь, склады, исследования и др.).
11. `screenshots/` — индекс (`index.json`, `index.md`) и ключевые кадры окна 7 суток (`annotations.jsonl`, `feed.md` при OCR).

Полное описание форматов и валидация приведены в [Контракте данных](docs/data-contract.md).

## Канонические проверки

```powershell
python tests/check_seed.py
python -m unittest discover -s tests
python -m tools.campaign check-schemas
git diff --check
```

## Документация

- [Контракт данных](docs/data-contract.md)
- [Архитектура](docs/architecture.md)
- [Требования](docs/requirements.md)
- [Критерии приёмки](docs/acceptance.md)
- [Настройки Kenshi](docs/kenshi-settings.md)
- [Картография](docs/cartography.md)
- [Лента скриншотов](docs/screenshots.md)
- [Синхронизация с Google Диском](docs/gdrive-sync.md)
- [Интерфейс чат-ИИ](docs/chatgpt-interface.md)
