# Kenshi Campaign

Локальная обработка сохранений Kenshi и публикация подтверждённого состояния для ChatGPT. Реализован сквозной конвейер: извлечение неизменяемых снимков (`kenshi-session/10`), реестр снимков и веток (`kenshi-index/1`), совместимый diff с сохранением устойчивых ID и защитой от reload (`kenshi-session-diff/5`), сборка географического справочника мира (`kenshi-world-reference/1`), пространственное разрешение позиций персонажей и построек (`kenshi-locations/1`, группы близости, перемещения), валидация пакета, детерминированный рендеринг русских `brief.md` и `squad.md`, атомарная публикация release bundle (`published/`) на Windows с защитой `.gitkeep` и rollback.

Нужны Python 3.12+ и `jsonschema` с Draft 2020-12 для проверки проектных схем. Для `export` установка игры и конвертер не требуются. Для `save` используется существующий `kenshi-platoon-export/tools/session_export.py`; бинарный парсер здесь не дублируется.

## Запуск в PowerShell

Команды выполняются из корня проекта. `.env.example` — шаблон; `.env` автоматически не загружается. Настройки передаются через окружение текущего процесса:

```powershell
$env:KENSHI_CONVERTER_DIR = Join-Path $HOME 'kenshi-platoon-export'
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'

# Сквозной срез конвейера: extract -> diff -> locate -> validate -> render -> publish
python -m tools.campaign pipeline --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --campaign-id hot-lon --branch-id main
python -m tools.campaign pipeline --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --campaign-id hot-lon --branch-id main

# Отдельные операции конвейера:
python -m tools.campaign build-world
python -m tools.campaign check-world --world-ref <world_ref_id_or_path>
python -m tools.campaign extract --source-kind export --source "$env:KENSHI_CONVERTER_DIR/kenshi-session.json" --campaign-id hot-lon --branch-id main
python -m tools.campaign diff --to <snapshot_id> [--from <parent_snapshot_id>]
python -m tools.campaign locate --current data/snapshots/<id>/current.json --world-ref <world_ref_id_or_path> [--changes data/diffs/<diff_id>.json] [--output data/locations/<id>.json]
python -m tools.campaign render --current data/snapshots/<id>/current.json --changes data/diffs/<diff_id>.json [--locations data/locations/<id>.json] --output-dir published
python -m tools.campaign publish --snapshot-id <snapshot_id> [--parent-snapshot-id <parent_id>] [--branch-id <branch_id>] [--world-ref <world_ref_id_or_path>]
python -m tools.campaign index show
python -m tools.campaign index rebuild [--snapshots-dir data/snapshots]
python -m tools.campaign index validate
python -m tools.campaign branch list
python -m tools.campaign branch switch <branch_id> [--create] [--name <name>]
python -m tools.campaign branch tree

# Авторская сессия связывает несколько снимков; заметки сами не создают событий:
python -m tools.campaign timeline session --session-id session-001 --snapshot-id <id1> --snapshot-id <id2>
python -m tools.campaign timeline append <event.json>
python -m tools.campaign timeline import <events.jsonl>
python -m tools.campaign timeline list --branch-id main --resolved
python -m tools.campaign timeline validate
```

Путь к конкретному входу обязателен: CLI не выбирает «последний» сейв и не устанавливает родителя по времени файла. Имена кампании/ветки задаёт пользователь. `--parent-snapshot-id` передаёт явного родителя; проверка реестра веток относится к задаче 3.1.

Умолчания конфигурации совпадают с показанными стандартными каталогами: конвертер в домашней папке, два корня сейвов, Steam Kenshi и Workshop `233860`. `KENSHI_SAVE_DIRS` разделяется символом `;` и задаёт защищённые корни. `--faction-sid` имеет приоритет над `KENSHI_FACTION_SID`, затем используется `204-gamedata.base`. Несовпадение фракции готового экспорта с эффективной настройкой вызывает отказ.

Результат: `data/snapshots/<snapshot_id>/` (снимки), `data/diffs/<diff_id>.json` (сравнения), `data/world_references/<world_ref_id>.json` (географические справочники), `data/locations/<loc_id>.json` (пространственные привязки) и `published/` (готовый release bundle: `manifest.json`, `current.json`, `changes.json`, `locations.json`, `world_reference.json`, `timeline.jsonl`, `brief.md`, `squad.md`). При ошибке код выхода — `1`, пакет не публикуется; диагностика выводится в stderr. Генерируемые файлы вручную не исправляются.

`timeline` (алиас `event`) ведёт append-only `timeline/events.jsonl`; `--root` после подкоманды задаёт другой корень кампании. `append` принимает JSON одного события, `import` — JSONL пакет с завершающим переводом строки. Формат показан в `examples/timeline-event.example.json`; его синтетические ID и источники необходимо заменить действительными. `observed` требует снимок/diff, `confirmed` — явно подтверждённую заметку/скриншот, `inferred` сохраняется как гипотеза. Сначала создаётся сессия, затем событие. `timeline session --notes <notes.md>` импортирует авторские заметки; без аргумента используется шаблон. Источники фактов должны существовать; абсолютные пути и выход из корня запрещены.

Исправление добавляется новым событием `kind="correction"`, `corrects_event_id=<исправляемый_ID>`. Повтор идентичного импорта ничего не меняет; конфликт ID, повреждённая строка или неоднозначная коррекция вызывают отказ. После сбоя журнал восстанавливается из проверенной копии владельцем данных. Публикацию запускает отдельная команда `publish` либо `pipeline`: она включает действующие факты только выбранной цепочки снимков. Гипотезы и события неопределённой/параллельной ветки остаются в исходном журнале. Подробная семантика описана в [контракте данных](docs/data-contract.md#подтверждённые-игровые-события).

Для другого журнала у `publish`/`pipeline` есть `--timeline <events.jsonl>` и `--sessions-root <корень_кампании>`. При повторном рендере опубликованного пакета передайте `render --timeline published/timeline.jsonl` вместе с `current`, `changes` и `locations`. Новый факт может изменить выпуск при прежнем `snapshot_id`: сверяйте также `generated_at` и SHA-256 файла из `artifacts.timeline`. Неизменное содержимое сохраняет дату и хеш выпуска.

## Регламент подключения и чтения пакета в ChatGPT (AC-13)

Для работы с ChatGPT используется самодостаточный release bundle из каталога `published/`, формируемый конвейером автоматически.

### Состав release bundle

В каталог `published/` входят ровно 8 согласованных артефактов и защитный `.gitkeep`:
1. `manifest.json` — корень выпуска: `format`, `snapshot_id`, `parent_snapshot_id`, `branch_id`, `generated_at`, `source_sha256`, `world_ref_id`, контрольный статус валидации `validation.status == "passed"` и реестр артефактов `artifacts`.
2. `current.json` — нормализованное состояние кампании (`kenshi-session/10`): отряд, базовые характеристики и навыки, экипировка, инвентарь, здоровье, деньги и постройки игрока.
3. `changes.json` — совместимый детерминированный diff (`kenshi-session-diff/5`) со статусом `valid: true`, устойчивыми ID и фиксацией изменений.
4. `locations.json` — пространственное разрешение персонажей и построек (`kenshi-locations/1`): 4 статуса достоверности (`in_location`, `near_location`, `in_region`, `unknown`), группы близости (`spatial_groups`) и прямолинейное перемещение (`movement`).
5. `world_reference.json` — версионированный справочник мира (`kenshi-world-reference/1`), согласованный с `world_ref_id` манифеста.
6. `timeline.jsonl` — отфильтрованная проекция журнала подтверждённых событий активной ветки (`kenshi-event/1`).
7. `brief.md` — лаконичная оперативная сводка на русском языке для контекста ChatGPT.
8. `squad.md` — подробная ролевая сводка по персонажам, экипировке и навыкам.

### Процедура передачи и верификации свежести

1. **Экспорт пакета**: запустить штатную команду обновления конвейера (`pipeline`). Убедиться, что код возврата `0`, а `validation.status` в `manifest.json` равен `"passed"`.
2. **Доставка через GitHub**: после успешной сверки агент проверяет состав изменений и исходящих коммитов, коммитит проверенный `published/` и выполняет обычный push в `asaf1n/kenshi-campaign-tech` по постоянному разрешению из `AGENTS.md`, без повторного запроса. Команда `pipeline` обновляет локальные файлы; Git-доставку выполняет агент отдельным шагом. Ошибка сети не отменяет локальную публикацию и должна быть явно указана. Сырой сейв и посторонние изменения не отправляются.
3. **Проверка чтения**: агент проверяет удалённый commit и читает `published/manifest.json` через доступное подключение GitHub; остальные артефакты читает с того же commit, чтобы не смешивать выпуски. В новом игровом Chat доступ к подключению и свежесть выпуска проверяются заново. Контрольный запрос:
   > *«Назови snapshot_id, generated_at, world_ref_id, игровой день/время и краткую сводку отрядов из manifest.json и brief.md»*
4. **Сверка ответа**: убедиться, что названные моделью `snapshot_id`, `generated_at` и `world_ref_id` совпадают с локальным `manifest.json`, а состав отрядов по группам близости — с `brief.md`. При недоступности GitHub резервный способ — прикрепить `manifest.json`, `brief.md`, `squad.md` либо полный пакет к чату или Project Sources. Ручная загрузка не является обязательным шагом при работающем подключении.
5. **Контроль свежести (freshness test)**: при добавлении событий в журнал `timeline/events.jsonl` без изменения сохранения Kenshi перевыпуск пакета сохраняет тот же `snapshot_id`, но гарантированно обновляет `generated_at` и хеш проекции `timeline.jsonl`. Ответ ChatGPT по устаревшему пакету мгновенно идентифицируется по несовпадающему `generated_at`.
6. **Граница проверки AC-13**: успешный push подтверждает доставку в GitHub, а чтение через подключение — доступ из проверенного чата. Они не доказывают автоматическую замену Project Sources, запись в Library или доступ в другом Chat/на Android. Для каждого непроверенного канала указывать ограничение. Память модели не заменяет чтение актуального выпуска.

## Пошаговое руководство по эксплуатации на Windows (AC-15)

### 1. Первичная настройка окружения

Требования:
- Windows 10/11 x64
- Python 3.12+ (проверить: `python --version`)
- Установленный пакет `jsonschema` с поддержкой Draft 2020-12: `pip install jsonschema format-nongraphical`
- Установленная Kenshi (GOG или Steam) и склонированный конвертер `kenshi-platoon-export`

Задайте переменные окружения в PowerShell (рекомендуется добавить в профиль `$PROFILE`):

```powershell
$env:KENSHI_CONVERTER_DIR = Join-Path $HOME 'kenshi-platoon-export'
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'
```

### 2. Сборка справочника мира (при установке или смене модов)

Справочник мира собирается непосредственно из файлов игры и активных модов без изменения файлов игры (read-only):

```powershell
python -m tools.campaign build-world
```

Справочник сохраняется в `data/world/<world_ref_id>.json` и используется конвейером автоматически.

### 3. Единая команда обновления после игровой сессии

После сохранения в игре (например, в слот `autosave0` или `quicksave`) запустите единую команду конвейера:

```powershell
python -m tools.campaign pipeline `
  --source-kind save `
  --source "$env:LOCALAPPDATA/kenshi/save/autosave0" `
  --campaign-id default `
  --branch-id main
```

Команда автоматически выполняет:
- Read-only проверку и извлечение данных (`extract`).
- Регистрацию снимка в `data/index.json` (`index`).
- Детерминированный расчет диффа с предыдущим снимком ветки (`diff`).
- Пространственное разрешение персонажей и построек (`locate`).
- Детерминированный рендеринг `brief.md` и `squad.md` (`render`).
- Валидацию схем Draft 2020-12, приватности и согласованности (`validate`).
- Атомарную публикацию release bundle в каталог `published/` (`publish`).

### 4. Работа с ветками и альтернативными сценариями

Просмотр доступных веток и активной ветки:
```powershell
python -m tools.campaign branch list
```

Создание новой ветки (например, перед рискованным походом или откатом):
```powershell
python -m tools.campaign branch switch expedition-south --create --name "Экспедиция на юг"
```

Отображение дерева снимков и веток:
```powershell
python -m tools.campaign branch tree
```

### 5. Добавление авторских событий и заметок

1. Создать сессию, связывающую снимки:
   ```powershell
   python -m tools.campaign timeline session --session-id session-001 --snapshot-id <snapshot_id>
   ```
2. Добавить подтвержденное событие в журнал:
   ```powershell
   python -m tools.campaign timeline append path/to/event.json
   ```
3. Проверить целостность журнала:
   ```powershell
   python -m tools.campaign timeline validate
   ```

### 6. Диагностика ошибок и восстановление

- **Ненулевой exit code (`1`)**: конвейер завершился с ошибкой, публикация отклонена, stderr содержит точную причину отказа (нарушение схемы, несовместимый родитель, конфликт веток или попытка записи).
- **Сохранность предыдущей публикации**: транзакционный механизм публикации гарантирует, что каталог `published/` и `.gitkeep` остаются в последнем заведомо валидном состоянии. Смешанных манифестов не возникает.
- **Восстановление реестра снимков (`data/index.json`)**: в случае повреждения файла индекса выполните:
  ```powershell
  python -m tools.campaign index rebuild
  ```
  Индекс будет полностью детерминированно перестроен из неизменяемых `provenance.json` всех ранее сохранённых снимков.
- **Отказ при автосохранении**: если игра перезаписывает сейв во время чтения, конвейер мгновенно фиксирует несовпадение SHA-256 и завершает работу с кодом `1` без выпуска недостоверных данных. Повторите команду после завершения записи в Kenshi.

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
python tests/check_e2e_real.py --report tests/.evidence/e2e_real.json
```

`check_live.py` сравнивает байты двух запусков с `PYTHONHASHSEED=1/42` и хеши входов. `check_converter.py` проверяет настоящий внешний конвертер на двух синтетических бинарных сейвах и минимальном справочнике игры; это не проверка настоящих игровых сейвов и не сверка с UI.

## Файлы и документация

- `tools/`: CLI, конфигурация, адаптер, нормализация, файловые границы и валидация схем.
- `contracts/`, `examples/`: схемы index, manifest, timeline-event, world-reference, locations и синтетические примеры.
- `tests/`: проверки; `tests/.evidence/` — локальные результаты вне Git.
- `data/`, `published/`, `timeline/`, `sessions/`: снимки, публикация, append-only журнал и отдельно авторские заметки с привязками к снимкам.
- [Контракт данных](docs/data-contract.md), [архитектура](docs/architecture.md), [требования](docs/requirements.md), [критерии приёмки](docs/acceptance.md).
