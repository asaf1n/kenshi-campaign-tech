# Kenshi Campaign

Локальная обработка сохранений Kenshi и публикация проверенных данных для чат-ИИ моделей (LLM ассистентов). Конвейер извлекает данные из сейва, регистрирует снимки, сравнивает совместимые состояния по устойчивым ID, учитывает перезагрузки по откату игрового времени, разрешает игровые координаты, формирует сводки `brief.md` и `squad.md` и атомарно публикует пакет в `published/`. Версии форматов и структура полей определяются [контрактом данных](docs/data-contract.md).

Требуются Python 3.12+ и `jsonschema` с поддержкой Draft 2020-12 для валидации всех контрактов. Для `save` используется нативный изолированный движок `tools/` (прямое чтение бинарных сохранений и игровых баз данных Kenshi без внешних конвертеров). Для `export` поддерживается чтение готовых выгрузок JSON.

## Быстрый старт в PowerShell

Запуск одной командой: [Initialize-Pipeline.ps1](Initialize-Pipeline.ps1) находит каталог игры, берёт сохранение кампании (папка сохранений общая для всех прохождений: явно `-SaveFolder <имя или шаблон>`, `KENSHI_SAVE_PATTERN` или `KENSHI_SAVE_NAME`, при нескольких совпадениях свежайшее из совпавших; без явного выбора перебираются до `-MaxCandidates` (6) свежих папок командой `python -m tools.campaign check-save`, берётся первая, относящаяся к кампании, иначе ошибка со списком проверенных папок и долей), выполняет `pipeline` (разбор сохранения и скриншотов в `published/`) и доставляет результат в Kenshi Compain. `campaign_id` берётся из последнего `published/manifest.json`; кампания ведётся одной линией, родитель нового снимка — голова кампании в `data/index.json`. Перед публикацией сейв сверяется с родительским снимком; чужой сейв отклоняется (код 1), `-AllowNewCampaign` / `--allow-new-campaign` пропускает проверку. `-CheckOnly` только проверяет все кандидаты (до `-MaxCandidates`, без остановки на первой подходящей), печатает вердикты `check-save` и итог «выбран бы: <первая подходящая>» (код 0 - подходящая найдена, 1 - нет), не запуская pipeline, доставку и Диск и ничего не записывая.

```powershell
.\Initialize-Pipeline.ps1
```

Основной канал доставки - GitHub (проекты ChatGPT читают `main` и `published/` через `Kenshi-Compain`); Google Диск - односторонняя резервная копия `main` и выгрузки в нативных форматах Google (Docs, Sheets), источник блокнотов Gemini при возврате к Gemini (работа с Gemini приостановлена). Шаг Диска (`--sync-drive`) выполняется по умолчанию; недоступный Диск или Google API (нет токена, сети, `G:`) даёт предупреждение `drive: skipped|failed (причина)`, а не отказ: публикация и доставка считаются успешными, код возврата 0. Диагностика квоты и отложенного Google - [docs/gdrive-sync.md](docs/gdrive-sync.md).

Все переменные окружения опциональны. `.env.example` содержит значения; `.env` читается при наличии. Канонический запуск на локальной машине пользователя:

```powershell
$env:KENSHI_DIR = 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
$env:KENSHI_WORKSHOP_DIR = 'C:\Program Files (x86)\Steam\steamapps\workshop\content\233860'
$env:KENSHI_SAVE_DIRS = (Join-Path $env:LOCALAPPDATA 'kenshi\save') + ';' + (Join-Path $env:KENSHI_DIR 'save')
$env:KENSHI_FACTION_SID = '204-gamedata.base'

# Единый конвейер одной командой: extract -> diff -> locate -> validate -> render -> publish -> deliver
python -m tools.campaign pipeline --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/autosave0" --campaign-id example-campaign --deliver-to "C:\Users\artem\Kenshi Compain"

# Либо по отдельным шагам:
python -m tools.campaign build-world
python -m tools.campaign check-world --world-ref <world_ref_id_or_path>
python -m tools.campaign extract --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/autosave0"
python -m tools.campaign diff --to <snapshot_id> [--from <parent_snapshot_id>]
# Дифференциальный анализ за произвольный интервал дней (P0 #4):
python -m tools.campaign period-diff --from-day 107 --to-day 127 [--campaign-id hot-lon]
python -m tools.campaign locate --current data/snapshots/<id>/current.json --world-ref <world_ref_id_or_path> [--changes data/diffs/<diff_id>.json] [--output data/locations/<id>.json]
python -m tools.campaign render --current data/snapshots/<id>/current.json --changes data/diffs/<diff_id>.json [--locations data/locations/<id>.json] --output-dir published
python -m tools.campaign publish [--snapshot-id <snapshot_id>] [--world-ref <world_ref_id_or_path>] [--screens-dir <screens_dir>]
python -m tools.campaign deliver-companion [--companion-dir "C:\Users\artem\Kenshi Compain"] [--push]
python -m tools.campaign index show
python -m tools.campaign index rebuild [--snapshots-dir data/snapshots]
python -m tools.campaign index validate
python -m tools.campaign index migrate [--apply]

# Работа с таймлайном и подтверждёнными событиями; авторские заметки не затираются:
# Быстрая фиксация подтверждённого события (P0 #5):
python -m tools.campaign timeline record --summary "Штурм лаборатории завершён" --day 128 --status confirmed
python -m tools.campaign timeline session --session-id session-001 --snapshot-id <id1> --snapshot-id <id2>
python -m tools.campaign timeline append <event.json>
python -m tools.campaign timeline import <events.jsonl>
python -m tools.campaign timeline list [--snapshot-id <snapshot_id>] --resolved
python -m tools.campaign timeline validate
```

Каждая подкоманда детерминированна: повторный запуск с теми же входами даёт побайтово идентичный результат. Идентичность снимка — содержимое сейва и параметры разбора; кампания и родитель в неё не входят. Родитель назначается при регистрации: голова кампании в `data/index.json`. Перезагрузка определяется по откату игрового времени относительно родителя ([ADR](docs/adr/campaign-line.md)).

Параметры окружения имеют предсказуемые значения по умолчанию: автопоиск в реестре и AppData, стандартные каталоги Steam Kenshi и Workshop `233860`. `KENSHI_SAVE_DIRS` поддерживает разделение через `;` для нескольких путей сохранений. `--faction-sid` переопределяет переменную окружения `KENSHI_FACTION_SID`, по умолчанию `204-gamedata.base`. Выходные каталоги не могут перекрываться с защищёнными источниками данных игры.

Артефакты: `data/snapshots/<snapshot_id>/` (снимки), `data/diffs/<diff_id>.json` (разницы), `data/world_references/<world_ref_id>.json` (справочники мира), `data/locations/<loc_id>.json` (разрешения локаций) и `published/` (готовый release bundle: `manifest.json`, `extended/current.json`, `changes.json`, `changes.md`, `locations.json`, `world_reference.json`, `location_registry.json`, `timeline.jsonl`, `brief.md`, `squad.md`, `tables/`, `screenshots/` с `index.json`, `index.md` и ключевыми кадрами за последние 7 суток; `annotations.jsonl` и `feed.md` только при содержательном OCR; `screenshots_latest.pdf` вне git). При ошибках конвейер возвращает ненулевой exit code `1`, изменения откатываются; сообщения об ошибках выводятся в stderr. Снимки сохранений и справочники мира неизменяемы.

Таймлайн `timeline` (или `event`) ведётся append-only в `timeline/events.jsonl`; `--root` задаёт корень кампании с `data/`, `timeline/` и `sessions/`. `append` добавляет одно JSON-событие, `import` импортирует JSONL с проверкой схемы каждого события. Формат задаётся схемой `contracts/timeline-event.schema.json`; валидный пример в `examples/timeline-event.example.json`; схема требует устойчивый ID и источник с уровнем подтверждения. `observed` фиксирует данные сейва/diff, `confirmed` — проверенные человеком действия/решения, `inferred` — гипотезы модели. Факты не перезаписываются. `timeline session --notes <notes.md>` регистрирует границы игровой сессии; текст заметок сохраняется. Игровые события получают источник; вывод модели не считается фактом без подтверждения человеком.

Коррекции событий регистрируются с `kind="correction"`, `corrects_event_id=<целевой_ID>`. Они не мутируют исторические строки append-only журнала; целевой ID, тип коррекции и обоснование явно валидируются и отображаются в истории. Команда `timeline list --resolved` выполняет свёртку цепочек коррекций. Конвейеры `publish` и `pipeline` поддерживают прямую интеграцию: проверенные события таймлайна автоматически попадают в публикуемый бандл. Пустые строки и некорректные ID блокируются валидатором. Порядок оформления описан в [контракте данных](docs/data-contract.md#таймлайн-и-события).

Интеграция таймлайна в `publish`/`pipeline` выполняется через `--timeline <events.jsonl>` и `--sessions-root <каталог_сессий>`. Экспорт подтверждённых событий цепочки публикуемого снимка выполняется в `published/timeline.jsonl` с валидацией схемы, а `render --timeline published/timeline.jsonl` встраивает события в `brief.md` наряду с `current`, `changes` и `locations`. Публикация детерминированна и строго привязана к `snapshot_id`: при отсутствии изменений `generated_at` и SHA-256 сохраняются в `artifacts.timeline`. Попытка публикации некорректного таймлайна прерывает конвейер.

## Визуальная лента скриншотов (`published/screenshots/`)

Конвейер публикации автоматически формирует визуальную ленту ключевых кадров игры для контекста LLM:
- **Источник**: стандартная папка `<KENSHI_DIR>/_screens` (например, `C:\Program Files (x86)\Steam\steamapps\common\Kenshi\_screens`) либо каталог из переменной `KENSHI_SCREENS_DIR` / флага `--screens-dir`. Каталог всегда используется в режиме **read-only**, оригинальные скриншоты никогда не изменяются и не удаляются.
- **Окно 7 суток и ключевые кадры**: из изображений (`.jpg`, `.jpeg`, `.png`) моложе 7 суток (`now - 7d`) детерминированно отбираются 20–30 ключевых кадров (по эпизодам и равномерно по времени); остальные в выпуск не попадают. Старые кадры удаляются из релиза.
- **Уменьшение и архив**: кадры уменьшаются до 1600 px по длинной стороне (JPEG). Полный уменьшенный набор окна хранится в `data/screenshots_archive/` вне git (каталог можно связать junction с диском, синхронизируемым с облачным хранилищем). Для обработки нужен пакет `av` (`pip install av`).
- **Google API (задача 07, этап 0)**: `pip install google-api-python-client google-auth-oauthlib google-auth-httplib2`; разовый вход `python scripts/gdrive_auth.py` (client_secret и token.json — в `%LOCALAPPDATA%kenshi-campaign-tech`, вне репозитория).
- **Артефакты**:
  - `published/screenshots/index.json` — машиночитаемый хронологический индекс (`kenshi-screenshots-index/1`) с метаданными, хешами SHA-256 и прямыми Raw/Blob URL.
  - `published/screenshots/index.md` — новые кадры снимка и архивный хвост с первичной разметкой и ссылками; `annotations.jsonl` и `feed.md` публикуются только при содержательном OCR.
  - `published/screenshots/screenshots_latest.pdf` — кадры дельты с текстовой шапкой; вне git, копия на Google Диске ([docs/gdrive-sync.md](docs/gdrive-sync.md)).
  - `published/screenshots/annotations.jsonl` и `feed.md` — машинная разметка кадров (день, время, выбранный персонаж, задачи) и лента серий; публикуются при содержательном OCR. Локальный индекс всех кадров: `python -m tools.campaign screenshots-index --all`, поиск: `screenshots-find` (`docs/screenshots.md`).
  - `published/screenshots/*.jpg` — уменьшенные ключевые кадры; имя `<исходное имя>-<8 символов sha256 оригинала>.jpg`.
- **Доступ LLM**: изображения открываются по прямым Raw-ссылкам репозитория Kenshi Compain (`https://raw.githubusercontent.com/asaf1n/Kenshi-Compain/...`) либо загружаются в чат.

## Разделение контекста: игровой компаньон Kenshi-Compain

Для предотвращения смешивания инженерного и игрового контекста авторские документы кампаний (`Campaign/`), хроники, билды и системные инструкции для LLM-чата вынесены в специализированный репозиторий:
🔗 **Канонический игровой репозиторий:** [asaf1n/Kenshi-Compain](https://github.com/asaf1n/Kenshi-Compain)

Репозиторий `kenshi-campaign-tech` является автономным инженерным конвертером (upstream) и поставляет проверенный срез состояния `published/`.

## Доставка проверенных данных (AC-13)

Конвейер готовит проверенное игровое состояние в виде release bundle в каталоге `published/` и осуществляет одностороннюю доставку в репозиторий `Kenshi-Compain` через команду:
```powershell
python -m tools.campaign deliver-companion [--push]
```
или автоматически флагом `--deliver-to "C:\Users\artem\Kenshi Compain"` при выполнении `pipeline`.

Вместе с выгрузкой доставка генерирует `docs/reference/encounters.md` в `Kenshi-Compain` из таблиц справочника юнитов (пять из семи: `squad_*`, `faction_*`; `tools/encounters.py`, подробнее — `docs/data-contract.md`); файл входит в тот же коммит и не затрагивается, если таблиц в выпуске нет. Картографические производные `docs/reference/regions_map.svg` и `regions_adjacency.json` доставляются в том же коммите при совместимом просмотрщике `sites/map/index.html`; их состояние — поле `cartography` отчёта доставки (`docs/cartography.md`).

### Состав release bundle

Каталог `published/` содержит 11 взаимосвязанных артефактов (файлы и каталоги: `extended/`, `tables/` — по 1 пункту) и служебный `.gitkeep`. Каталог `published/` генерируется конвейером, в `kenshi-campaign-tech` не коммитится (`.gitignore`), доставляется в `Kenshi-Compain` через `deliver-companion`:
1. `manifest.json` — машинный манифест публикации: `format`, `snapshot_id`, `parent_snapshot_id`, `campaign_id`, `reload` (у снимка перезагрузки), `generated_at`, `source_sha256`, `world_ref_id`, статус валидации `validation.status == "passed"` и реестр артефактов `artifacts`.
2. `extended/current.json` (очищенный полный срез, читать выборочно) — данные опубликованного снимка: участники, характеристики, здоровье, экипировка, инвентари, исследования, собственность и доступные сведения о мире.
3. `changes.json` — структурный diff выбранных совместимых снимков, включая статус сравнения, устойчивые ID и ограничения сравниваемых категорий; `changes.md` — его читаемое зеркало. Отношения фракций в diff — сырые значения `relation`.
4. `locations.json` — пространственная привязка участников и зданий, группы местоположений и изменения координат.
5. `world_reference.json` — справочник географии мира (локации, регионы), соответствующий `world_ref_id` манифеста; растр `regions_map` и `source_hashes` в публикацию не входят (полный файл в `data/world/`).
6. `timeline.jsonl` — опубликованная проекция подтверждённых событий цепочки снимка.
7. `brief.md` — компактная текстовая сводка для быстрого контекста чат-ИИ модели.
8. `squad.md` — подробная текстовая роспись отряда, снаряжения и характеристик.
9. `tables/` — 20 файлов (19 CSV и `index.md`): отряд, экипировка с итоговыми колонками `calc_*`, инвентари, расы, склады, базовые цены, сырые торговые записи, уникальные NPC, Town Overrides, дипломатия (сырой `relation`), условия реакций мира и справочник юнитов (7 таблиц). Формат, колонки и правила — [контракт данных](docs/data-contract.md), словарь колонок — `tables/index.md`. Оценок и классов конвертера в публикации нет: таблицы читаются и оцениваются потребителем самостоятельно. Таблицы цен, торговли, реакций и справочника юнитов строятся по установленной игре и модам снимка (кэш `data/cache`); без них они пусты, предупреждение — в `manifest.validation.warnings` и `tables/index.md`.
10. `screenshots/` — лента кадров (см. выше).
11. `location_registry.json` — реестр всех локаций справочника с признаком обнаружения из сейва (место открыто на игровой карте). Доставка добавляет отметки зачистки, подтверждённые при `[итог]` в игровом репозитории. [Контракт реестра](docs/data-contract.md#реестр-локаций-и-ручные-отметки).

### Регламент доставки и верификации

1. **Локальный конвейер**: запустить конвейер обновления (`pipeline`). Убедиться, что код возврата `0`, а `validation.status` в `manifest.json` равен `"passed"`.
2. **Доставка в GitHub**: после успешной верификации локально сформированного `published/` выполняются обычные commit и push без повторного запроса; `pipeline` сам Git не вызывает. Односторонняя доставка в `Kenshi-Compain` выполняется автоматически через `deliver-companion`.
3. **Проверка удалённого выпуска**: проверить коммит и публикацию `published/manifest.json` в репозитории на GitHub; убедиться, что удалённый хеш коммита соответствует локальному.
4. **Сверка свежести (freshness test)**: чат-ИИ ассистент считывает актуальные `snapshot_id`, `generated_at` и `world_ref_id` из `manifest.json` и `brief.md` через подключённый проект или веб-интерфейс, подтверждая актуальность игрового дня и времени.

## Инструкция по развёртыванию под Windows (AC-15)

### 1. Подготовка окружения

Требования:
- Windows 10/11 x64
- Python 3.12+ (проверка: `python --version`)
- Установленные зависимости: `pip install jsonschema format-nongraphical av reportlab`
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

Справочник запишется в `data/world_references/<world_ref_id>.json` и станет доступен конвейеру.

### 3. Регулярный запуск конвейера из сохранения

При появлении нового сохранения (например, после игровой сессии `autosave0` или `quicksave`) конвейер запускается одной командой:

```powershell
python -m tools.campaign pipeline `
  --source-kind save `
  --source "$env:LOCALAPPDATA/kenshi/save/autosave0" `
  --campaign-id default
```

Шаги конвейера выполняются автоматически:
- Read-only прямое извлечение данных сохранения нативным движком (`extract`).
- Регистрация в индексе `data/index.json` (`index`).
- Расчёт дифференциального состояния (`diff`).
- Пространственная привязка (`locate`).
- Рендеринг `brief.md` и `squad.md` (`render`).
- Валидация по JSON Schema Draft 2020-12, проверка приватности (`validate`).
- Атомарная публикация release bundle в каталог `published/` (`publish`).
- Односторонняя доставка в `Kenshi-Compain` при наличии флага `--deliver-to`.

### 4. Линия кампании и миграция индекса

Кампания ведётся одной линией: каждый новый снимок становится потомком головы кампании. Перезагрузка старого сохранения распознаётся по откату игрового времени; более поздние снимки исключаются из цепочки. Индекс прежнего формата `kenshi-index/1` переводится одной командой:
```powershell
python -m tools.campaign index migrate          # план
python -m tools.campaign index migrate --apply  # резервная копия, запись, проверка
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
python tests/check_live.py --source-kind save --source "$env:LOCALAPPDATA/kenshi/save/quicksave" --output tests/.evidence/check_live/snapshots --report tests/.evidence/save.json
python tests/check_converter.py --report tests/.evidence/converter.json
python tests/check_e2e_real.py --report tests/.evidence/e2e_real.json
```

`check_live.py` подтверждает неизменность результатов при `PYTHONHASHSEED=1/42` и изоляцию чтения. Сквозные проверки пишут снимки, дельты и индекс только во временные каталоги; рабочие `data/` не затрагиваются. `check_converter.py` подтверждает неизменность исходников нативного движка; `check_e2e_real.py` проверяет полный сквозной сценарий на реальных данных Kenshi.

## Структура репозитория

- `Initialize-Pipeline.ps1`: запуск конвейера и доставка в Kenshi Compain одной командой.
- `Save-Packet.ps1`: буфер обмена -> пакет в `tmp/inbox` Диска -> `ingest-inbox` -> отчёт (принят с SHA / отклонён с причиной); `-Accept <файл>` снимает защиты приёма, `-NoPush` коммитит без push ([docs/gdrive-sync.md](docs/gdrive-sync.md)).
- `tools/`: нативный изолированный движок (бинарный парсер GameData, индексатор GameDB, SaveReader, расчет характеристик экипировки, расчет протезов, табличные выгрузки (`tables.py`), экспорт сессий, diff, географический локатор, публикатор, таймлайн, доставщик в Kenshi-Compain).
- `contracts/`, `examples/`: схемы index, manifest, timeline-event, world-reference, locations и валидные примеры.
- `tests/`: модульные и интеграционные тесты; `tests/.evidence/` содержит доказательства прогонов для аудита.
- `data/`, `published/`, `timeline/`, `sessions/`: снимки, публикации, append-only журнал событий и протоколы сессий.
- [Контракт данных](docs/data-contract.md), [Настройки Kenshi](docs/kenshi-settings.md), [Архитектура](docs/architecture.md), [Требования](docs/requirements.md), [Критерии приёмки](docs/acceptance.md), [Синхронизация с Google Диском](docs/gdrive-sync.md), [Интерфейс чат-ИИ](docs/chatgpt-interface.md).
