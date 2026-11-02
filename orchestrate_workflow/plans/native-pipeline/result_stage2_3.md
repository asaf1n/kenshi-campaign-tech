# Результат этапа 2.3: Модуль инфраструктуры и производства баз (принят независимым ревью)

## 1. Реализованные изменения
- `tools/save_reader.py`:
  - `read_player_property`:
    - Приоритет чтения реальных имен построек из строковой секции `b_str["name"]` («Хранение: доспехи», «Хранение: еда», «Campfire» и др.) с отсечением технического инстанса `r["name"] == "0"`.
    - Добавлено отдельное поле `template_name`: имя шаблона из GameDB.
    - `usage_nodes`: динамическое извлечение узлов анимации 3D-модели Kenshi (`usenode004` и др.) через `indexed(b_str, "usagenode_")` с числовым порядком ключей.
    - Энергетика: извлечение `power_on` (тумблер питания), `gets_battery` (подключение к батареям) и `battery_charge` (фактический заряд аккумуляторов, например 120.0). В документации зафиксировано, что `battery_charge == 0.0` у станков/костров — штатное значение движка, а не разряженная батарея.
    - Производство: извлечение `is_paused`, `is_repeat`, `production_progress`, `consumption` (словарь `consumption amount-*`), `resource_mult`, `mining_resource_level`.
    - Состояние и оборона: `complete` (подтвержденная готовность), `destroyed`, `construction_progress_raw` (сырой показатель стройматериалов, у готовых построек > 1.0), `current_shots`, `is_locked`, `lock_level`, `hardness`.
  - `collect_items`:
    - Округление `quality` и `charges` через `_r()`.
    - `wear`, `dam`, `stun`: опциональное извлечение float-параметров (отдают `None`, если ключи отсутствуют у неповреждённых предметов хранилищ).
    - `unique`: признак уникального предмета (`True` или `None`).
    - `owned_by`: 5-компонентный handle владельца предмета (`ownedbyTYPE..CS`) с нейтральной семантикой без неподтверждённых утверждений о краже.
    - `raw`: изолированная копия исходных словарей (`bool`, `float`, `int`, `string`).
- `tests/test_base_production_extraction.py`: 4 теста:
  - `test_collect_items_synthetic_rich_attributes`: синтетическая проверка сбора качественных атрибутов (оружие с wear/dam/stun/owned_by; аптечки с charges без износа; пенька со стаком).
  - `test_building_template_resolution_synthetic`: синтетическая проверка разрешения имени постройки и `template_name` через GameDB для безымянных объектов (`r.name == "0"`).
  - `test_real_save_buildings_and_infrastructure`: проверка на `autosave0`:
    - 157 построек базы игрока (45 верхнего уровня + 112 интерьера).
    - Более 100 построек с непустыми читаемыми именами из `string.name` (`name != "0"`).
    - 2 аккумулятора с зарядом 120.0, `power_on=True`, `gets_battery=True`, `complete=True`.
    - Недостроенная турель (шаблон `2870-gamedata.base` / sid `2074023-zone18.35-INGAME-S23`) с `assertIsNotNone`: `complete=False`, `is_paused=True`, `power_on=False`, `construction_progress_raw > 1.0`.
    - Верстак робототехники (sid `2577638-zone18.35-INGAME-S23`) с `assertIsNotNone`: `complete=True`, `power_on=True`, `is_paused=False`, `is_repeat=False`, `usage_nodes`.
  - `test_real_save_storage_inventories`: проверка на `autosave0`:
    - Более 200 предметов в хранилищах; более 30 предметов с `owned_by`.
    - 577 ед. пеньки (`1965-gamedata.base`), 41 ед. медикаментов (`1359-gamedata.base`, `515-gamedata.base`), 19 ед. древних книг (`43953-rebirth.mod`).
- `docs/data-contract.md`:
  - Зафиксирован контракт нативного SaveReader для инфраструктуры, энергетики, производства и складских запасов.
  - Устранена трактовка `owned_by` как краденого; описана семантика `wear`/`dam`/`stun` как опциональных полей; зафиксировано значение `battery_charge == 0.0` у станков; отмечено, что публикуемый `current.json` сохраняет формат `kenshi-session/10` до этапа 03.

## 2. Верификация
1. `python -m unittest tests/test_base_production_extraction.py -v`: 4/4 теста пройдены успешно (OK, 0.69s).
2. `python -m unittest discover -s tests -v`: 81/81 тест пройден успешно (OK).
3. `git diff --check`: 0 замечаний.
