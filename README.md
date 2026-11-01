# Kenshi Campaign

Локальная обработка сохранений Kenshi и публикация подтверждённого состояния для ChatGPT. Реализован полный вертикальный срез Этапа 1.2: извлечение неизменяемых снимков (`kenshi-session/10`), совместимый diff с сохранением устойчивых ID (`kenshi-session-diff/5`), валидация пакета, детерминированный рендеринг русских `brief.md` и `squad.md`, атомарная публикация release bundle (`published/`) на Windows с защитой `.gitkeep` и rollback.

Нужны Python 3.12+ и `jsonschema` с Draft 2020-12 для проверки проектных схем. Для `export` установка игры и конвертер не требуются. Для `save` используется существующий `kenshi-platoon-export/tools/session_export.py`; бинарный парсер здесь не дублируется.

## Запуск в PowerShell

Команды выполняются из корня проекта. `.env.example` — шаблон; `.env` автоматически не загружается. Настройки передаются через окружение текущего процесса:

```powershell
$env:KENSHI_CONVERTER_DIR = Join-Path $HOME 'kenshi-platoon-export'
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'

# Сквозной срез конвейера: extract -> diff -> validate -> render -> publish
python -m tools.campaign pipeline --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --campaign-id hot-lon --branch-id main
python -m tools.campaign pipeline --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --campaign-id hot-lon --branch-id main

# Отдельные операции конвейера:
python -m tools.campaign extract --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --campaign-id hot-lon --branch-id main
python -m tools.campaign diff --to <snapshot_id> [--from <parent_snapshot_id>]
python -m tools.campaign render --current data/snapshots/<id>/current.json --changes data/diffs/<diff_id>.json --output-dir published
python -m tools.campaign publish --snapshot-id <snapshot_id> [--parent-snapshot-id <parent_id>]
```

Путь к конкретному входу обязателен: CLI не выбирает «последний» сейв и не устанавливает родителя по времени файла. Имена кампании/ветки задаёт пользователь. `--parent-snapshot-id` передаёт явного родителя; проверка реестра веток относится к задаче 3.1.

Умолчания конфигурации совпадают с показанными стандартными каталогами: конвертер в домашней папке, два корня сейвов, Steam Kenshi и Workshop `233860`. `KENSHI_SAVE_DIRS` разделяется символом `;` и задаёт защищённые корни. `--faction-sid` имеет приоритет над `KENSHI_FACTION_SID`, затем используется `204-gamedata.base`. Несовпадение фракции готового экспорта с эффективной настройкой вызывает отказ.

Результат: `data/snapshots/<snapshot_id>/` (снимки), `data/diffs/<diff_id>.json` (сравнения) и `published/` (готовый release bundle: `manifest.json`, `current.json`, `changes.json`, `brief.md`, `squad.md`). При ошибке код выхода — `1`, пакет не публикуется; диагностика выводится в stderr. Генерируемые файлы вручную не исправляются.

## Проверки

```powershell
python tests/check_seed.py
python -m unittest discover -s tests
python -m tools.campaign check-schemas
python tests/run_evidence.py
git diff --check
```

Unit-проверки используют временные фикстуры; тест Windows junction проверяет запрет выхода в сейв через ссылку. Дополнительные прогоны с явными доступными входами:

```powershell
python tests/check_live.py --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --output data/snapshots --report tests/.evidence/export.json
python tests/check_live.py --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --output data/snapshots --report tests/.evidence/save.json
python tests/check_converter.py --converter "$env:KENSHI_CONVERTER_DIR" --report tests/.evidence/converter.json
```

`check_live.py` сравнивает байты двух запусков с `PYTHONHASHSEED=1/42` и хеши входов. `check_converter.py` проверяет настоящий внешний конвертер на двух синтетических бинарных сейвах и минимальном справочнике игры; это не проверка настоящих игровых сейвов и не сверка с UI.

## Файлы и документация

- `tools/`: CLI, конфигурация, адаптер, нормализация, файловые границы и валидация схем.
- `contracts/`, `examples/`: схемы manifest/event и синтетические примеры.
- `tests/`: проверки; `tests/.evidence/` — локальные результаты вне Git.
- `data/`, `published/`, `timeline/`, `sessions/`: снимки, будущая публикация и отдельно авторские материалы.
- [Контракт данных](docs/data-contract.md), [архитектура](docs/architecture.md), [требования](docs/requirements.md), [критерии приёмки](docs/acceptance.md).
