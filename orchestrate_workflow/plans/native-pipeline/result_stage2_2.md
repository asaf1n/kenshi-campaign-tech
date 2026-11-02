# Результат этапа 2.2: Расширение извлечения персонажей (доработка по замечаниям ревью)

## 1. Реализованные изменения
- `tools/task_types.py`:
  - Канонический словарь `TASK_TYPE_NAMES` (ровно 289 задач, 0..288) и функция `get_task_name(code)`, сгенерированные напрямую из `fcs_enums.def` игры Kenshi.
- `tools/converter_worker.py`:
  - `tools/task_types.py` добавлен в состав `ENGINE_FILES` для детерминированного хеширования движка.
- `tools/save_reader.py`:
  - `clean_handle`: универсальная функция очистки безадресных handle (с `TYPE == 11` для задач типа `JOB_MEDIC`, `JOB_REPAIR_ROBOT` или с нулевыми координатами C, S, I, CS) в `None`. Сохранён алиас `clean_job_handle = clean_handle`.
  - `decode_jobs(jobs_rec)`: структурированное декодирование очередей работ `primary` и `temporary` персонажа с именами задач (`JOB_MEDIC`, `JOB_REPAIR_ROBOT`, `OPERATE_MACHINERY`, `OPERATE_STORAGE`, `JOB_BUILDER`) и очищенными `target_handle` целевых станков/грядок/контейнеров. Поле `raw` создаёт независимые копии словарей записи (`dict(j_int)`, `dict(j_str)`, `dict(j_bool)`).
  - `parse_bounties(st_str, st_int)`: извлечение наград за голову по индексированным ключам `bountyfac0..N`, `bountyexp0..N`, `crimes0..N` со списком `entries`, агрегированным `has_bounty` и `total_amount`.
  - `parse_character`:
    - `carrying`: структурированное извлечение физической переноски: `is_carrying` строго по флагу `bool carrying`, `target_handle` у несущего персонажа и `carried_by` у того, кого несут (проверено на паре Хоббс — «Вынос мозга» в `quicksave`). Очистка через универсальный `clean_handle`.
    - `bounties`: полное декодирование через `parse_bounties` (подтверждены награды Хоббса и мультинаграды Пиа от СШ и Шеков).
    - `combat_modes`: булевы флаги боевого ИИ (`taunt`, `passive`, `ranged`, `defensive`, `chase`, `stealth`, `non_combat`), читаемые напрямую из булевой секции записи 36. При отсутствии ключа возвращается `None` (без домысливания `False`). Несуществующий режим `hold` удалён.
- `tests/test_character_jobs_and_carrying.py`: 7 тестов:
  - Проверка словаря (ровно 289 задач).
  - Проверка `clean_handle` и `clean_job_handle` (None для TYPE=11 и нулевых C, S, I, CS).
  - Синтетическое декодирование очередей работ с очисткой глобальных задач и изолированным `raw`.
  - Синтетическая проверка наград от нескольких фракций.
  - Синтетическая проверка взаимной переноски (несущий с `target_handle` и переносимый с `carried_by`) и проверка режимов с `None` при отсутствии ключей.
  - Интеграционный тест на `autosave0`: Хоббс (медик с `target_handle is None`), Доктор Чон, Мью, Зелёный палец, Зелёный (`ranged == True`, отсутствие `hold`).
  - Интеграционный тест на `quicksave`: Хоббс (`is_carrying == True`, `target_handle` с TYPE=1, награда 457 кат), «Вынос мозга» (`is_carrying == False`, `carried_by` с TYPE=1), Пиа (2 награды на сумму 3713 кат).
- `docs/data-contract.md`:
  - Полностью синхронизирован с реализованной структурой персонажа: описаны актуальные формы `bounties` (`entries`, `has_bounty`, `total_amount`), `carrying` (`target_handle`, `carried_by`, `raw`), `jobs` (`clean_handle`, изолированный `raw`) и `combat_modes` (без вымышленного `hold`, с `null` при отсутствии ключей). Отмечено, что семантика кодов `crimes` на текущем этапе не подтверждена.

## 2. Верификация
1. `python -m unittest tests/test_character_jobs_and_carrying.py -v`: 7/7 тестов пройдены успешно (OK, 0.75s).
2. `python -m unittest discover -s tests -v`: 77/77 тестов пройдены успешно (OK).
3. `git diff --check`: 0 замечаний.
