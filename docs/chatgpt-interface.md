# Публикация данных для интерфейса игрового компаньона (Kenshi Compain)

## Разделение контекста и двухрепозиторная архитектура

Игровое сопровождение, художественная хроника, анализ билдов и интерактивные инструкции компаньона вынесены в отдельный специализированный репозиторий:
🔗 **Канонический игровой репозиторий:** [asaf1n/Kenshi-Compain](https://github.com/asaf1n/Kenshi-Compain)

Репозиторий `kenshi-campaign-tech` выполняет роль автономного инженерного конвертера (upstream) и поставляет проверенный срез состояния `published/`.

Проекты ChatGPT читают GitHub `main` (`published/` и документы кампании) в `Kenshi-Compain`.

## Поставляемые артефакты (`published/`, контракт `kenshi-publication/2`)

При успешном прогоне конвейера (`pipeline`) в целевой каталог публикации формируется неизменяемый бандл из проверенных файлов:
1. `manifest.json` (`kenshi-publication/2`) — метаданные контрольной точки (`snapshot_id`, `parent_snapshot_id`, `campaign_id`, `game_time`, признак перезагрузки `reload`, `world_ref_id`, реестр `artifacts`, статус валидации `validation.status`).
2. `extended/current.json` (`kenshi-session/11`; для импортированных старых выгрузок поддерживается `kenshi-session/10`) — очищенный полный срез сохранения; читать только выборочно, когда данных сводок и таблиц недостаточно.
3. `brief.md` — текстовая сводка ключевых изменений и оперативной обстановки.
4. `squad.md` — детализированная ведомость бойцов, навыков, травм и экипировки.
5. `changes.json` (`kenshi-session-diff/5`) — дифференциальный срез относительно предыдущего снимка цепочки; `changes.md` — его читаемое зеркало с разделом «Промежуточные снимки» по окну цепочки.
6. `locations.json` (`kenshi-locations/1`) — разрешённые пространственные координаты участников отряда и построек игрока, пространственные группы `spatial_groups` и прямолинейное смещение `movement`.
7. `world_reference.json` (`kenshi-world-reference/1`) — статический справочник географии мира (261 локация и палитра регионов `BIOME_GROUP` без растра `regions_map` и `source_hashes`).
8. `location_registry.json` (`kenshi-location-registry/3`) — реестр всех 261 локаций справочника мира с флагом `discovered` из `Town state.discovered` сохранения, шаблонным владельцем `template_faction` и отметками зачистки `cleared` (`evidence`, `game_time`), накладываемыми при доставке из `Campaign/<кампания>/locations_progress.json` формата `kenshi-location-progress/3`.
9. `unique_entities_registry.json` (`kenshi-unique-entities-registry/2`, поддерживается чтение `/1`) — уникальные NPC, боссы и значимые персонажи, статические привязки, возможные владельцы Meitou, наблюдаемые экземпляры и подтверждённое происхождение трофеев. Доставка накладывает ручные свидетельства зачистки только на связи с локациями и согласованно пересобирает `tables/unique_entities.csv`.
10. `timeline.jsonl` (`kenshi-event/1`) — опубликованная проекция подтверждённых игровых событий и автоматических событий дельт окна цепочки.
11. `tables/` — табличные выгрузки состояния (24 CSV в UTF-8 и `tables/index.md` со словарём колонок); перечень ниже.
12. `screenshots/` — 20–30 ключевых кадров окна 7 суток, уменьшенных до 1600 px (`index.json` формата `kenshi-screenshots-index/1` и `index.md` всегда; `annotations.jsonl` формата `kenshi-screenshot-annotation/1` и `feed.md` только при распознанном тексте). Машинная разметка даёт день и время с экрана, выбранного персонажа и задачи; это навигация, а не просмотр кадра и не подтверждённое событие.

## Разграничение ролей географических и справочных артефактов для ИИ-агента

- `published/locations.json` (`kenshi-locations/1`) — **текущее положение отряда и построек игрока** в конкретном снимке (`in_location`, `near_location`, `in_region`, `unknown`), группировка участников `spatial_groups` и смещение `movement` между снимками. Не хранит историю посещений и статус зачистки объектов.
- `published/unique_entities_registry.json` (`kenshi-unique-entities-registry/2`) — **потенциальные боссы, уникальные NPC и Meitou**: статус зачистки места не означает поражения NPC; для оружия отдельны наблюдаемый экземпляр и подтверждённое происхождение.
- `published/location_registry.json` (`kenshi-location-registry/3`) — **реестр известности и зачистки всех 261 локаций мира**: флаг `discovered` (`true`/`false` из `Town state.discovered` сейва — открыто ли место на карте игры), `template_faction` (шаблонный владелец в данных мира) и поле `cleared` (`evidence`, `game_time`), накладываемое при доставке из ручного слоя `Campaign/<кампания>/locations_progress.json` (`kenshi-location-progress/3`) по подтверждённым при `[итог]` результатам зачистки.
- `published/world_reference.json` (`kenshi-world-reference/1`) — **статический паспорт локаций и регионов** (координаты, зоны `col.row`, `confirmed_radius`, `radius_source`, `static_buildings`, палитра `regions`), неизменный для данного набора файлов игры и модов (`world_ref_id`).
- Производные справочники в `docs/reference/` игрового репозитория (доставляются `deliver-companion` вне `published/`):
  - `docs/reference/encounters.md` — сводка встреч, состава отрядов по регионам и городам и базовых отношений фракций, собираемая из таблиц `published/tables/`.
  - `docs/reference/regions_map.svg` и `docs/reference/regions_adjacency.json` (`kenshi-region-adjacency/2`) — векторные контуры регионов и граф смежности регионов по 4-связности растра игры с длиной общих границ `border_lengths` (deliver-companion обновляет их при совместимом просмотрщике `sites/map/index.html`).

## Табличные выгрузки (`tables/`)

`tables/index.md` — вход: `snapshot_id`, игровое время, число строк, словарь колонок, неопубликованные поля. Таблицы:

| Файл | Назначение |
|---|---|
| `squad.csv` | персонаж отряда: раса, местоположение, кровь, сытость, статусы, работы, атрибуты и навыки |
| `equipment.csv` | все экземпляры оружия, брони и протезов (`location_kind`: worn, inventory, backpack, belt, storage): `level`, `grade`, `manufacturer`, значения шаблона `tpl_*`, итоговые `calc_*` |
| `buildings.csv`, `research.csv`, `build_catalog.csv`, `biome_resources.csv` | постройки и станки игрока, дерево технологий, каталог строительства и природные ресурсы регионов |
| `unique_entities.csv` | табличная проекция окончательного реестра уникальных NPC, боссов и Meitou после доставки; учитывает формат `/2` |
| `inventory.csv` | все предметы персонажей поэкземплярно (контейнер, слот, уровень, качество, количество); ключ сравнения снимков — (`character_id`, `base_sid`, `level`, `slot`, `dup_no`), `item_sid` действует только внутри снимка |
| `races.csv` | расы: иммунитеты к погоде, естественная броня, скорости, множители, анатомия |
| `storage.csv` | предметы в хранилищах зданий игрока по поселениям, с количеством, уровнем, качеством, грейдом и базовой ценой |
| `item_prices.csv` | базовые цены записей игры |
| `trade_modifiers.csv` | сырые торговые записи игры (цены продажи и покупки не вычисляются) |
| `unique_npcs.csv` | уникальные NPC: `state_code` (0 — мёртв, 1 — жив и свободен, 2 — в плену), `player`, `captured_by` |
| `town_overrides.csv` | города, заменённые записями override |
| `diplomacy.csv` | сырые `relation`, `trust`, `trust_neg` фракций к игроку |
| `world_reactions.csv`, `world_reaction_links.csv` | условия вариантов городов и текущее состояние NPC из условий `NPC is` |
| `unit_templates.csv`, `unit_template_gear.csv`, `squad_templates.csv`, `squad_members.csv`, `squad_spawns.csv`, `faction_relations.csv`, `faction_hostility.csv` | справочник юнитов: шаблоны персонажей и отрядов, места появления, отношения и настройки фракций |

Правила чтения:

- Таблицы содержат значения игры; классов, ролей («союзник», «враг»), рейтингов и выводов конвертера в них нет. Оценки, сравнения и рекомендации строятся по значениям самостоятельно, с указанием файла и строки-источника.
- Пустая ячейка — «не получено или не применимо», а не ноль. Связывать строки разных таблиц по `sid`/`id`, не по имени.
- `equipment.csv`: `calc_*` использовать только при `calc_status` = `verified`. `unverified_class` — сочетание не сверено с игрой, `unknown_level` — неизвестен `level`, `unknown_template` — нет шаблона; в этих случаях `calc_*` пусты, считать их самостоятельно по `tpl_*` нельзя без оговорки о неподтверждённой формуле.
- Цены оружия и брони с уровнем в таблицах нет; `item_prices.csv` и `base_price` — базовые значения записей.
- `world_reaction_links.csv` показывает требуемое и текущее состояние NPC рядом; выполнение условия не вычисляется.

## Доставка в Kenshi-Compain

Канонический запуск полного цикла (выбор подходящего сейва `check-save`, `pipeline`, доставка в `Kenshi-Compain` с наложением `locations_progress.json` на `location_registry.json`, генерацией `docs/reference/encounters.md` и обновлением картографических артефактов) выполняется одной командой:
```powershell
.\Initialize-Pipeline.ps1
```

Также доступна прямая доставка уже собранного `published/`:
```powershell
python -m tools.campaign deliver-companion [--push]
```
или запуск `pipeline` с флагом `--deliver-to`:
```powershell
python -m tools.campaign pipeline `
  --source-kind save `
  --source "$env:LOCALAPPDATA\kenshi\save\<slot_name>" `
  --campaign-id hot-lon `
  --deliver-to "C:\Users\artem\Kenshi Compain" `
  --deliver-push
```

Файлы в `published/` являются строго read-only для любых LLM-чатов.
