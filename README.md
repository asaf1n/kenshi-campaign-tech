# Kenshi Campaign

Локальная обработка сохранений Kenshi и публикация подтверждённого состояния для чат-ИИ моделей (LLM ассистентов). Включает полный цикл: прямое извлечение снимка (`kenshi-session/10`), регистрацию и версионирование (`kenshi-index/1`), строгий diff с разделением по устойчивым ID персонажей и изоляцией веток reload (`kenshi-session-diff/5`), пространственную привязку к географии мира (`kenshi-world-reference/1`), определение местоположений и перемещений (`kenshi-locations/1`, 16 ориентиров, группы), детерминированный рендеринг контекстных сводок `brief.md` и `squad.md`, атомарную публикацию release bundle (`published/`) под Windows с сохранением `.gitkeep` и rollback.

Требуются Python 3.12+ и `jsonschema` с поддержкой Draft 2020-12 для валидации всех контрактов. Для `save` используется нативный изолированный движок `tools/` (прямое чтение бинарных сохранений и игровых баз данных Kenshi без внешних конвертеров). Для `export` поддерживается чтение готовых выгрузок JSON.

## Быстрый старт в PowerShell

Все переменные окружения опциональны. `.env.example` содержит значения; `.env` читается при наличии. Канонический запуск на локальной машине пользователя:

```powershell
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'

# Единый конвейер одной командой: extract -> diff -> locate -> validate -> render -> publish
python -m tools.campaign pipeline --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/autosave0" --campaign-id hot-lon --branch-id main

# Либо по отдельным шагам:
python -m tools.campaign build-world
python -m tools.campaign check-world --world-ref <world_ref_id_or_path>
python -m tools.campaign extract --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/autosave0" --campaign-id hot-lon --branch-id main
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

# Работа с таймлайном и подтверждёнными событиями; авторские заметки не затираются:
python -m tools.campaign timeline session --session-id session-001 --snapshot-id <id1> --snapshot-id <id2>
python -m tools.campaign timeline append <event.json>
python -m tools.campaign timeline import <events.jsonl>
python -m tools.campaign timeline list --branch-id main --resolved
python -m tools.campaign timeline validate
```

Каждая подкоманда детерминированна: повторный запуск с теми же входами даёт побайтово идентичный результат. Режим кампании/ветки обязателен. `--parent-snapshot-id` вычисляется автоматически по индексу; ручное переопределение используется только для ветвлений.

Параметры окружения имеют предсказуемые значения по умолчанию: автопоиск в реестре и AppData, стандартные каталоги Steam Kenshi и Workshop `233860`. `KENSHI_SAVE_DIRS` поддерживает разделение через `;` для нескольких путей сохранений. `--faction-sid` переопределяет переменную окружения `KENSHI_FACTION_SID`, по умолчанию `204-gamedata.base`. Выходные каталоги не могут перекрываться с защищёнными источниками данных игры.

Артефакты: `data/snapshots/<snapshot_id>/` (снимки), `data/diffs/<diff_id>.json` (разницы), `data/world_references/<world_ref_id>.json` (справочники мира), `data/locations/<loc_id>.json` (разрешения локаций) и `published/` (готовый release bundle: `manifest.json`, `current.json`, `changes.json`, `locations.json`, `world_reference.json`, `timeline.jsonl`, `brief.md`, `squad.md`). При ошибках конвейер возвращает ненулевой exit code `1`, изменения откатываются; сообщения об ошибках выводятся в stderr. Снимки сохранений и справочники мира неизменяемы.

Таймлайн `timeline` (или `event`) ведётся append-only в `timeline/events.jsonl`; `--root` позволяет направить события в отдельную ветку или сессию. `append` добавляет одно JSON-событие, `import` импортирует JSONL с проверкой схемы каждого события. Формат задаётся схемой `contracts/timeline-event.schema.json`; валидный пример в `examples/timeline-event.example.json`; схема требует устойчивый ID и источник с уровнем подтверждения. `observed` фиксирует данные сейва/diff, `confirmed` — проверенные человеком действия/решения, `inferred` — гипотезы модели. Факты не перезаписываются, события ветвятся. `timeline session --notes <notes.md>` регистрирует границы игровой сессии; текст заметок сохраняется. Игровые события получают источник; вывод модели не считается фактом без подтверждения человеком.

Коррекции событий регистрируются с `kind="correction"`, `corrects_event_id=<целевой_ID>`. Они не мутируют исторические строки append-only журнала; целевой ID, тип коррекции и обоснование явно валидируются и отображаются в истории. Команда `timeline list --resolved` выполняет свёртку цепочек коррекций. Конвейеры `publish` и `pipeline` поддерживают прямую интеграцию: проверенные события таймлайна автоматически попадают в публикуемый бандл. Пустые строки и некорректные ID блокируются валидатором. Порядок оформления описан в [контракте данных](docs/data-contract.md#таймлайн-и-события).

Интеграция таймлайна в `publish`/`pipeline` выполняется через `--timeline <events.jsonl>` и `--sessions-root <каталог_сессий>`. Экспорт подтверждённых событий для текущей ветки выполняется в `published/timeline.jsonl` с валидацией схемы, а `render --timeline published/timeline.jsonl` встраивает события в `brief.md` наряду с `current`, `changes` и `locations`. Публикация детерминированна и строго привязана к `snapshot_id`: при отсутствии изменений `generated_at` и SHA-256 сохраняются в `artifacts.timeline`. Попытка публикации некорректного таймлайна прерывает конвейер.

## Постоянные авторские документы кампании

Для межсессионного хранения подтверждённой истории, ролей, планов и методики используется `Campaign/Хроники Хот Лон/` с файлами `Kenshi_Хроника.md`, `Kenshi_Отряд.md`, `Kenshi_Текущая_арка.md`, `Kenshi_Режимы_и_методика.md`. Это авторские файлы, не часть автоматически создаваемого `published/`; `pipeline` их не переписывает.

В новом сеансе чат-ИИ модели при необходимости считывай актуальные четыре файла из одного Git-коммита. Для текущих числовых данных независимо проверяй `published/manifest.json` и используй артефакты его выпуска. При подтверждённом обновлении авторских данных меняй существующие имена без параллельных копий, проверяй diff, обычный commit и удалённое чтение. Запись в GitHub сама по себе не меняет локальные файлы `Kenshi Compain` или Project Sources.

## Доставка и интеграция с чат-ИИ моделями (AC-13)

Конвейер готовит проверенное игровое состояние для чат-ИИ моделей (LLM ассистентов) в виде release bundle в каталоге `published/`, доставляемого через GitHub.

Постоянные файлы отдельных кампаний хранятся в Git сервиса в `\Campaign\<campaign_name>\`. Это обеспечивает локальный запуск конвейера на ПК игрока, в то время как снимок и история прохождения доступны чат-ИИ ассистенту из любой среды через GitHub в реальном времени.

### Состав release bundle

Каталог `published/` содержит 8 взаимосвязанных файлов и служебный `.gitkeep`:
1. `manifest.json` — машинный манифест публикации: `format`, `snapshot_id`, `parent_snapshot_id`, `branch_id`, `generated_at`, `source_sha256`, `world_ref_id`, статус валидации `validation.status == "passed"` и реестр артефактов `artifacts`.
2. `current.json` — полное состояние текущего снимка (`kenshi-session/10`): персонажи, характеристики и навыки без округления, экипировка, инвентари, исследования, здания и припасы отряда.
3. `changes.json` — подтверждённый структурный diff (`kenshi-session-diff/5`) с флагом `valid: true`, устойчивыми ID персонажей и обнаружением reload.
4. `locations.json` — пространственная привязка отряда и зданий (`kenshi-locations/1`): 4 статуса разрешения (`in_location`, `near_location`, `in_region`, `unknown`), пространственные группы (`spatial_groups`) и вектор перемещения (`movement`).
5. `world_reference.json` — подтверждённый справочник географии мира (`kenshi-world-reference/1`), соответствующий `world_ref_id` манифеста.
6. `timeline.jsonl` — журнал подтверждённых событий кампании текущей ветки (`kenshi-event/1`).
7. `brief.md` — компактная текстовая сводка для быстрого контекста чат-ИИ модели.
8. `squad.md` — подробная текстовая роспись отряда, снаряжения и характеристик.

### Регламент доставки и верификации

1. **Локальный конвейер**: запустить конвейер обновления (`pipeline`). Убедиться, что код возврата `0`, а `validation.status` в `manifest.json` равен `"passed"`.
2. **Доставка в GitHub**: после успешной верификации локальный коммит проверенного состояния `published/` и данных кампании фиксируется в репозитории, и выполняется `git push` в удалённый репозиторий `asaf1n/kenshi-campaign-tech` всегда сразу без повторного запроса. Команда `pipeline` сама Git не вызывает; доставка выполняется сразу после проверки.
3. **Проверка удалённого выпуска**: проверить коммит и публикацию `published/manifest.json` в репозитории на GitHub; убедиться, что удалённый хеш коммита соответствует локальному.
4. **Сверка свежести (freshness test)**: чат-ИИ ассистент считывает актуальные `snapshot_id`, `generated_at` и `world_ref_id` из `manifest.json` и `brief.md` через подключённый проект или веб-интерфейс, подтверждая актуальность игрового дня и времени.

## Инструкция по развёртыванию под Windows (AC-15)

### 1. Подготовка окружения

Требования:
- Windows 10/11 x64
- Python 3.12+ (проверка: `python --version`)
- Установленные зависимости: `pip install jsonschema format-nongraphical`
- Установленная Kenshi (GOG или Steam) и сохранения игры.

Настройка переменных окружения в PowerShell (можно добавить в `$PROFILE`):

```powershell
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'
```

### 2. Сборка справочника мира (один раз для версии игры/модов)

Справочник строится нативно по установленным файлам игры и модам (read-only):

```powershell
python -m tools.campaign build-world
```

Справочник запишется в `data/world/<world_ref_id>.json` и станет доступен конвейеру.

### 3. Регулярный запуск конвейера из сохранения

При появлении нового сохранения (например, после игровой сессии `autosave0` или `quicksave`) конвейер запускается одной командой:

```powershell
python -m tools.campaign pipeline `
  --source-kind save `
  --source "$env:LOCALAPPDATA/kenshi/save/autosave0" `
  --campaign-id default `
  --branch-id main
```

Шаги конвейера выполняются автоматически:
- Read-only прямое извлечение данных сохранения нативным движком (`extract`).
- Регистрация в индексе `data/index.json` (`index`).
- Расчёт дифференциального состояния (`diff`).
- Пространственная привязка (`locate`).
- Рендеринг `brief.md` и `squad.md` (`render`).
- Валидация по JSON Schema Draft 2020-12, проверка приватности (`validate`).
- Атомарная публикация release bundle в каталог `published/` (`publish`).

### 4. Управление ветками прохождения

Просмотр существующих веток:
```powershell
python -m tools.campaign branch list
```

Создание ветки (например, для альтернативного пути или исследования):
```powershell
python -m tools.campaign branch switch expedition-south --create --name "Экспедиция на юг"
```

Отображение дерева ветвления:
```powershell
python -m tools.campaign branch tree
```

### 5. Регистрация событий таймлайна

1. Регистрация игровой сессии, если велись заметки:
   ```powershell
   python -m tools.campaign timeline session --session-id session-001 --snapshot-id <snapshot_id>
   ```
2. Добавление подтверждённого события:
   ```powershell
   python -m tools.campaign timeline append path/to/event.json
   ```
3. Валидация цепочки:
   ```powershell
   python -m tools.campaign timeline validate
   ```

### 6. Диагностика и восстановление

- **Ненулевой exit code (`1`)**: операция прервана, изменения не применены, в stderr выведено понятное сообщение (нехватка файлов, повреждение сейва, несовпадение хешей или пересечение путей).
- **Сбой во время публикации**: атомарный откат гарантирует, что содержимое `published/` и служебный `.gitkeep` не повреждены и возвращены в исходное состояние. Повторите запуск после устранения причины.
- **Повреждение файла индекса (`data/index.json`)**: индекс восстанавливается детерминированно:
  ```powershell
  python -m tools.campaign index rebuild
  ```
  Индекс будет пересобран по сохранённым метаданным `provenance.json` каждого снимка.
- **Попытка модификации сохранения**: исходные сейвы открываются строго read-only, хеши проверяются до и после, любая попытка записи приводит к ошибке с кодом `1` и откату операции. Данные Kenshi защищены.

## Тестирование и верификация

```powershell
python tests/check_seed.py
python -m unittest discover -s tests
python -m tools.campaign check-schemas
python tests/run_evidence.py
git diff --check
```

Unit-тесты покрывают контракты; для Windows junction тестируется проверка отсутствия пересечения путей. Интеграционные тесты на реальных данных:

```powershell
python tests/check_live.py --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --output data/snapshots --report tests/.evidence/save.json
python tests/check_converter.py --report tests/.evidence/converter.json
python tests/check_e2e_real.py --report tests/.evidence/e2e_real.json
```

`check_live.py` подтверждает неизменность результатов при `PYTHONHASHSEED=1/42` и изоляцию чтения. `check_converter.py` подтверждает неизменность исходников нативного движка; `check_e2e_real.py` проверяет полный сквозной сценарий на реальных данных Kenshi.

## Структура репозитория

- `tools/`: нативный изолированный движок (бинарный парсер GameData, индексатор GameDB, SaveReader, расчет характеристик экипировки, расчет протезов, экспорт сессий, diff, географический локатор, публикатор, таймлайн).
- `contracts/`, `examples/`: схемы index, manifest, timeline-event, world-reference, locations и валидные примеры.
- `tests/`: модульные и интеграционные тесты; `tests/.evidence/` содержит доказательства прогонов для аудита.
- `data/`, `published/`, `timeline/`, `sessions/`: снимки, публикации, append-only журнал событий и протоколы сессий.
- [Контракт данных](docs/data-contract.md), [Настройки Kenshi](docs/kenshi-settings.md), [Архитектура](docs/architecture.md), [Требования](docs/requirements.md), [Критерии приёмки](docs/acceptance.md).
