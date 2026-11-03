# 06-cumulative-timeline-and-branch-switch: Накопительная хронология и детектор смены веток

## Цель
Решить проблему пустой хронологии `published/timeline.jsonl` и отсутствия регистрации развилок кампании. Обеспечить детерминированную сборку цепочки событий таймлайна по дереву снимков с соблюдением контракта `kenshi-event/1`, а также встроить детектор смены ветки сохранения, регистрирующий объект `branch_switch` в манифесте публикации.

## Решения
- 2026-11-03. **Детерминированная модель генерации `timeline.jsonl`**:
  - Таймлайн формируется не через мутабельную дозапись в `published/`, а детерминированно восстанавливается публикатором из индекса снимков ветки (`data/snapshots/` и цепочки `parent_snapshot_id`).
  - Повторный запуск конвейера на тех же исходных данных гарантированно дает идентичный побайтовый файл `published/timeline.jsonl`.
- 2026-11-03. **Соблюдение контракта `contracts/timeline-event.schema.json` (`kenshi-event/1`)**:
  - События, синтезируемые публикатором из дельт `changes.json`, оформляются строго по действующей схеме:
    - `format`: `"kenshi-event/1"`;
    - `event_id`: детерминированный ID события;
    - `snapshot_id`: sha256 текущего среза;
    - `session_id`: идентификатор сессии;
    - `recorded_at`: ISO timestamp;
    - `game_day`: числовой день из среза;
    - `kind`: тип события (`state_delta`, `location_change`, `squad_change`, `branch_switch`);
    - `status`: `"inferred"` для автоматических расчетных дельт diff; `"confirmed"` для подтвержденных хроникальных событий;
    - `summary`: лаконичное описание на русском языке;
    - `evidence`: массив подтверждений `[{"kind": "diff", "ref": snapshot_id}]`.
  - В схему `contracts/timeline-event.schema.json` опционально добавляются свойства `game_time` (строка формата `Day N HH:MM`) и `branch_id` (строка) для сохранения точной хронологической шкалы без нарушения обратной совместимости.
- 2026-11-03. **Детектор смены ветки (`branch_switch`)**:
  - Условие фиксации смены ветки:
    1. Игровое время `game_time` текущего снимка меньше `game_time` предыдущего снимка в цепочке (откат сохранения / reload).
    2. ИЛИ `parent_snapshot_id` не совпадает с последним зарегистрированным снимком текущей активной ветки.
  - В `contracts/manifest.schema.json` регистрируется опциональный объект:
    ```json
    "branch_switch": {
      "type": "object",
      "required": ["from_branch", "to_branch", "reason"],
      "properties": {
        "from_branch": {"type": "string"},
        "to_branch": {"type": "string"},
        "from_snapshot": {"type": "string"},
        "to_snapshot": {"type": "string"},
        "reason": {"type": "string", "enum": ["time_rollback", "parent_mismatch"]}
      },
      "additionalProperties": false
    }
    ```
  - При обнаружении смены ветки публикатор записывает объект `branch_switch` в `manifest.json` и генерирует специальное событие с `kind: "branch_switch"` в `timeline.jsonl`.
- 2026-11-03. Синхронно обновляются `docs/data-contract.md` и `docs/architecture.md`.

## Этапы задачи

1. **Расширение схемы и контракта (`contracts/timeline-event.schema.json`, `contracts/manifest.schema.json`, `docs/data-contract.md`)**. Добавить опциональные поля `game_time`, `branch_id` в `timeline-event.schema.json`. Зарегистрировать объект `branch_switch` в схеме манифеста.
2. **Генератор событий таймлайна из дельт diff (`tools/timeline.py`)**. Реализовать детерминированное извлечение значимых изменений из `changes.json` в формате `kenshi-event/1` со статусом `inferred` и ссылкой `evidence`.
3. **Детектор смены веток и детерминированная сборка (`tools/publisher.py`)**. Встроить проверку отката времени и несовпадения `parent_snapshot_id`. Формировать `branch_switch` в манифесте и собирать непрерывную цепочку `timeline.jsonl` по истории ветки.
4. **Тесты и верификация (`tests/test_timeline.py`)**.
   - Тест линейной цепочки (сборка непрерывной истории снимков).
   - Тест пропуска снимка (корректная привязка по дереву родителей).
   - Тест отката игрового времени / reload (генерация `branch_switch` и события в таймлайне).
   - Тест идемпотентности (повторный прогон конвейера выдает идентичный `timeline.jsonl`).
