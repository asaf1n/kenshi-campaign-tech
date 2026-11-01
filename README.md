# Kenshi Campaign

Локальное извлечение состояния Kenshi для последующей публикации в ChatGPT. Реализованы импорт `kenshi-session/10`, read-only адаптер внешнего конвертера и неизменяемые снимки. Diff, Markdown-рендер и выпуск `published/` относятся к следующим этапам.

Нужны Python 3.12+ и `jsonschema` с Draft 2020-12 для проверки проектных схем. Для `export` установка игры и конвертер не требуются. Для `save` используется существующий `kenshi-platoon-export/tools/session_export.py`; бинарный парсер здесь не дублируется.

## Запуск в PowerShell

Команды выполняются из корня проекта. `.env.example` — шаблон; `.env` автоматически не загружается. Настройки передаются через окружение текущего процесса:

```powershell
$env:KENSHI_CONVERTER_DIR = Join-Path $HOME 'kenshi-platoon-export'
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'

python -m tools.campaign extract --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --campaign-id hot-lon --branch-id main
python -m tools.campaign extract --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --campaign-id hot-lon --branch-id main
```

Путь к конкретному входу обязателен: CLI не выбирает «последний» сейв и не устанавливает родителя по времени файла. Имена кампании/ветки задаёт пользователь. `--parent-snapshot-id` передаёт явного родителя; проверка реестра веток относится к следующему этапу.

Умолчания конфигурации совпадают с показанными стандартными каталогами: конвертер в домашней папке, два корня сейвов, Steam Kenshi и Workshop `233860`. `KENSHI_SAVE_DIRS` разделяется символом `;` и задаёт защищённые корни. `--faction-sid` имеет приоритет над `KENSHI_FACTION_SID`, затем используется `204-gamedata.base`. Несовпадение фракции готового экспорта с эффективной настройкой вызывает отказ.

Результат: `data/snapshots/<snapshot_id>/{current.json,provenance.json}`. `--output` меняет корень снимков. Повтор не переписывает существующий снимок; другое содержимое при совпадающем ID вызывает ошибку. Снимки исключены из Git. При ошибке код выхода — `1`, снимок не регистрируется; диагностика выводится в stderr. Исправьте причину и повторите команду. Генерируемые файлы вручную не исправляются.

## Проверки

```powershell
python tests/check_seed.py
python -m unittest discover -s tests
python -m tools.campaign check-schemas
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
