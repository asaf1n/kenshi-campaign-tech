# Результат этапа 2.1: Модуль извлечения геополитики и дипломатии

## 1. Реализованные изменения
- `tools/save_reader.py`:
  - `classify_standing(relation)`: проектная классификация статуса отношений по порогам боевой механики Kenshi (`ally` >= 50.0, `friendly` > 0.0, `neutral` == 0.0, `unfriendly` > -30.0, `hostile` <= -30.0, `unknown` для null-значений).
  - `extract_diplomacy(records, player_faction_sid, gamedb_inst)`: извлечение дипломатической карты отношений фракций из записей типа 37 с сопоставлением по каноническому `gamedata stringID`. При отсутствии связи в сохранении relation сохраняется как `None`, standing — `"unknown"`, без подмены нулями.
  - `parse_uniques(state_rec)`: чтение `usedUniquesPlayer*` строго из секции `bool` (устранена ошибка чтения из string).
  - `extract_world_state(records, gamedb_inst)`: сбор 79 уникальных NPC с сырыми кодами состояния (0, 1, 2) без домысливания названий для неподтвержденных кодов; статус `state = "dead_or_captured"` присваивается строго подтвержденному коду `state_code == 2`; отбор переопределений городов (9 поселений с `replacementTown`, включая Бугристый Холм и Плохие Зубы). Сводка `summary` ведется по числовым кодам `state_0_count`, `state_1_count`, `state_2_count`.
  - Интеграция `diplomacy` и `world_state` в `parse_quick_save` и `read_quick_save`.
- `tests/test_diplomacy_extraction.py`: 4 теста (синтетические граничные условия и интеграционный тест на реальном сейве `autosave0`).
- `tests/test_save_reader.py`: синхронизирован тест синтетического разбора под чтение `usedUniquesPlayer` из `bool`.
- `docs/data-contract.md`: задокументированы структуры дипломатии и геополитики, правила порога классификации отношений и отказ от вымышленных статусов.

## 2. Верификация
1. `python -m unittest tests/test_diplomacy_extraction.py tests/test_save_reader.py -v`: 10/10 тестов пройдены успешно (OK, 1.88s).
2. `python -m unittest discover -s tests`: 70/70 тестов пройдены успешно (OK, 41.5s).
3. `git diff --check`: 0 замечаний.
