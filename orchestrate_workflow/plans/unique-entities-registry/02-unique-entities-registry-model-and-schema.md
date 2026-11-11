# 02-unique-entities-registry-model-and-schema

## Цель

Спроектировать и реализовать структуру нового публикуемого артефакта `published/unique_entities_registry.json` со схемой `contracts/unique-entities-registry.schema.json` (формат `kenshi-unique-entities-registry/1`):
1. **Схема и контракт**:
   - Корневые поля: `format` (`"kenshi-unique-entities-registry/1"`), `campaign_id`, `snapshot_id`, `world_ref_id`, `game_time`, `generated_at`, `unique_npcs`, `bosses`, `meitou_weapons`.
   - Строгая схема JSON Schema Draft 2020-12 с `"additionalProperties": false`.
   - Регистрация формата в `tools/validation.py`: добавление поддержки формата `"kenshi-unique-entities-registry/1"` в функцию `check_examples()` и белый список схем `validate()`.
2. **Группа 1: Уникальные NPC и рекруты (`unique_npcs`)**:
   - `npc_sid`: SID шаблона `CHARACTER`.
   - `name_en`, `name_ru`.
   - `category`: `"recruit"`, `"boss"`, `"world_leader"`, `"unique_npc"`.
   - `locations`: список привязок к объектам `instance_id` с полями:
     - `instance_id`, `template_sid`, `location_name_ru`, `location_name_en`, `region`;
     - `link_type`: `"resident"`, `"bar_squad"`, `"roaming"`, `"homeless"`;
     - `resolution`: `"unique"` (1:1), `"ambiguous"` (1:N при повторяющихся `template_sid`), `"unresolved"`;
     - `source_chain`: массив шагов цепочки (`CHARACTER -> SQUAD -> TOWN -> INSTANCE`).
   - `dynamic`:
     - `state_code`: `int | null` (0 мёртв, 1 жив и свободен, 2 в плену, null при отсутствии данных);
     - `player_defeat_flag`: `bool | null` (исходное значение `usedUniquesPlayer`: true если повержен игроком);
     - `squad_status`: статус нахождения в отряде игрока:
       - `"confirmed_in_squad"`: подтверждено строгим совпадением полного пятикомпонентного `handle` (все элементы `int`, не все нули);
       - `"confirmed_not_in_squad"`: подтвержденный полный `handle` отсутствует среди персонажей отряда игрока;
       - `"unresolved"`: частичный или отсутствующий `handle` (содержит `None` или все нули), либо легаси-снимок без данных `handle`. Неполные значения отбрасываются до сопоставления;
     - `character_id`: ID персонажа отряда игрока (например, `ps:1441040000`), если нанят, иначе `null`;
     - `handle`: `[int, int, int, int, int] | null` (только полностью валидный список из 5 целых чисел, иначе `null`);
     - `captured_by`: имя/ID несущего персонажа или контейнера, либо `null`;
     - `snapshot_id`, `game_time`.
3. **Группа 2: Боссы (`bosses`)**:
   - `npc_sid`, `name_en`, `name_ru`.
   - `classification_origin`: происхождение классификации (`"game_bounty"` при `bounty > 0`, `"world_state"` при наличии в `WORLD_EVENT_STATE`, `"meitou_holder"` при оружии Cross, `"custom_catalog"` для подтвержденных игровых боссов).
   - `bounty`: сумма награды и фракции розыска (если есть).
   - `associated_world_states`: список `WORLD_EVENT_STATE` SID и их текущих состояний.
   - `static_locations`: список привязок к локациям с указанием однозначности (`resolution: "unique" | "ambiguous"`).
   - `observed_state`: текущий `state_code`, факт поражения от игрока (`player_defeat_flag`), текущий статус пленения.
   - `cleared_evidence`: ссылка на ручную отметку зачистки из `locations_progress.json`, если объект зачищен (без автоматического выставления `cleared`!).
4. **Группа 3: Оружие Мейто (`meitou_weapons`)**:
   - Полный статический каталог охватывает **все 26 уникальных шаблонов оружия** (`WEAPON`) класса Мейто, найденных в наборе модов GameDB, сопоставленных со **всеми 32 шаблонами штатных владельцев** (`CHARACTER` с `weapon level = Cross`).
   - Поля записи оружия:
     - `base_sid`: SID шаблона оружия `WEAPON`.
     - `grade_sid`: `"52293-rebirth.mod"` (Мейто).
     - `manufacturer_sid`: `"52288-rebirth.mod"` (Крест / Cross).
     - `name_ru`, `name_en`, `weapon_category`.
     - `static_holders`: список ВСЕХ штатных владельцев по GameDB (`list[dict]` со связями 1:N без произвольного выбора одного: например, `1020-gamedata.base` связан с тремя шаблонами Cross, `477-gamedata.base` — с двумя).
       - Поля элемента: `npc_sid`, `npc_name`, `town_instance_id`, `town_name`.
     - `observed_instances`: список фактически обнаруженных у игрока экземпляров в текущем снимке:
       - `item_sid`: динамический SID записи сохранения в текущем снимке;
       - `carrier_character_id`, `carrier_name`, `carrier_squad`;
       - `storage_location_key`, `storage_settlement`, `storage_building`;
       - `location_kind`: `"equipped"`, `"inventory"`, `"backpack"`, `"storage"`;
       - `slot` / `section`: `"back"`, `"hip"`, `"main"` и т.д.
     - `provenance_status`: `"confirmed_trophy"` (при наличии подтвержденного события цепочки) либо `"observed_in_inventory"` (источник получения неизвестен).
     - `trophy_evidence`: объект ссылки на событие таймлайна цепочки публикуемого снимка:
       `{ "event_id": str, "day": int | null, "summary": str } | null`.
       - **Машиночитаемый протокол связывания без изменения схемы kenshi-event/1 и с соблюдением файловых проверок**:
         - Входные события `timeline/events.jsonl` полностью сохраняют схему `contracts/timeline-event.schema.json` и инварианты `tools/validation.py` (`_validate_evidence_ref` запрещает символы `\:<>"|?*`, а `_check_sources` в `timeline.py` требует существования реального файла подтверждения, например `sessions/<id>/notes.md` или `screenshots/<file>.jpg`).
         - Машиночитаемая связь события с трофеем задается через канонический маркер в тексте `summary`:
           `[base_sid=<sid>]` (регулярное выражение `r"\[base_sid=([a-zA-Z0-9_\-\.]+)\]"`), например:
           `"Добыт Крест паладина в бою с Лордом Фениксом [base_sid=52299-rebirth.mod]"`, при `status == "confirmed"` и валидном файловом источнике `evidence` (`user_note` / `screenshot`).
         - Сборщик сопоставляет активную цепочку снимка `timeline_data` (после `chain_events` / `project_timeline`).
         - Если подтверждённое событие с соответствующим `[base_sid=<sid>]` присутствует в цепочке снимка:
           `provenance_status: "confirmed_trophy"`, а в `trophy_evidence` записываются `event_id`, `day` и `summary` события.
         - Если такого события нет, либо если событие отменено коррекцией (`kind: "correction"`, `corrects_event_id`), либо при публикации раннего снимка (до дня события):
           статус строго равен `"observed_in_inventory"`, а `trophy_evidence: null` (источник получения честно остаётся неизвестным).
5. **Модуль `tools/unique_entities_registry.py`**:
   - Функция `build_unique_entities_registry(cur, world_ref, game_records, game_index, progress_data=None, timeline_data=None) -> dict[str, Any]`.
   - Принимает `timeline_data` (уже отфильтрованную цепочку событий из `publisher.py`), `game_index` (названия и категории всех предметов) и `game_records`.
   - Гарантия детерминированности, сортировки по ключам и устойчивости к отсутствию модов/локальных файлов.

## Решения

- 2026-11-11. Выделен самостоятельный артефакт `published/unique_entities_registry.json`. Контракты `kenshi-location-registry/3` и `kenshi-location-progress/3` остаются полностью неизменными во избежание поломки фронтенд-просмотрщика картографии (`check_viewer_compat`).
- 2026-11-11 (по замечанию ревьювера). Разделены понятия 26 шаблонов оружия и 32 шаблонов владельцев. Для оружия Мейто поле `static_holders` сделано списком `list[dict]` для сохранения связей 1:N без потери владельцев (например, `1020-gamedata.base` и `477-gamedata.base`).
- 2026-11-11 (по замечанию ревьювера). Добавлен статус `squad_status` с трехзначной логикой (`confirmed_in_squad`, `confirmed_not_in_squad`, `unresolved`), неполные `handle` отбрасываются до сопоставления.
- 2026-11-11 (по замечанию ревьювера). Зафиксирован машиночитаемый протокол связи трофея через маркер `[base_sid=<sid>]` в `summary` подтвержденного события, сохраняющий валидность схемы `kenshi-event/1`, безопасность `evidence.ref` и существование подтверждающих файлов. При отсутствии подтверждения в цепочке статус строго равен `"observed_in_inventory"`, `trophy_evidence: null`.

## Этапы задачи

1. Разработка схемы `contracts/unique-entities-registry.schema.json`, эталонного примера `examples/unique-entities-registry.example.json` и обновление `check_examples()` в `tools/validation.py`. Готово: `python -m tools.campaign check-schemas` валидирует новую схему и пример с кодом возврата 0. Принят.
2. Реализация сборщика `tools/unique_entities_registry.py` с поддержкой 1:N владельцев, `game_index`, трехзначной логики `squad_status`, контракта `trophy_evidence` через маркер `[base_sid=<sid>]` в `timeline_data` и детерминированной сортировки. Готово: модуль строит детерминированный реестр для снимка кампании со всеми 3 группами сущностей. Принят.
