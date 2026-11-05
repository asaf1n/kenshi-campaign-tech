# 01-remove-analytics

## Цель

Публикация и снимки, создаваемые новыми запусками, не содержат оценочных классов и расчётных показателей конвертера; остаются сырые `relation`, `trust`, `state_code`, исходные величины предметов, `changes.*` (без классов) и OCR-индекс. Старые снимки продолжают читаться и сравниваться.

## Решения

- 2026-11-05. Состав удаляемого определён инвентаризацией кода, а не списком задания: `classify_standing` и `summary.{allies,friendly,neutral,unfriendly,hostile,unknown}_count`; `standing` в записях отношений и в diff; блок «Союзники/Враги» в `brief.md`; `physics_calculated`, `combat_calculated`, модуль `tools/combat_analysis.py` и их вывод в `squad.md`. Источник: чтение `tools/save_reader.py`, `diff.py`, `rendering.py`, `converter_worker.py`. Предложение, ждёт ревью.
- 2026-11-05. `state`/`state_detail` уникальных NPC — удалить из публикации; остаются `state_code`, `player`, носитель пленника (решение пользователя 2026-11-05).
- 2026-11-05. Версия `kenshi-session/11` и совместимость старых снимков — вопрос 5 очереди. Решение пользователя: поле `standing` в схеме становится необязательным и не перечисляемым как требование, новые снимки его не пишут; повышение версии не требуется. Несовместимое изменение без миграции запрещено `AGENTS.md`.

## Этапы задачи

1. Источник отношений и дипломатия. Убрать `classify_standing`, `standing` и счётчики классов из `parse`/`diplomacy`; в `diff.py` сравнивать только `relation` (порог |Δ| ≥ 1,0) и корректно пропускать `standing` старых снимков; убрать вывод `standing` из `rendering.py` и `changes_render.py`; схему `session.schema.json` привести к необязательному `standing`; обновить `examples/session-11.example.json`. Готово: `grep -rn "standing\|allies_count\|hostile_count" tools contracts examples` не находит использований, кроме комментария о старых снимках; `python -m unittest tests.test_diff tests.test_diplomacy_extraction tests.test_contracts` проходит с исправленными ожиданиями; тест diff на паре старый-новый снимок сохраняется. Не начат.
2. Расчётные показатели. Удалить вызовы в `converter_worker.py`, модуль `combat_analysis.py` и `tests/test_combat_analysis.py`; убрать поля из схемы и примера, вывод из `rendering.py` (`render_squad`). Готово: в `extended/current.json`, `current/characters/*`, `squad.md` нет ключей `*_calculated` и строк «Физические показатели»/«Требования оружия»/«Штрафы брони»; `check-schemas` проходит. Не начат.
3. Блок «Союзники/Враги» и интерпретации NPC. Удалить блок из `render_brief`, вместо него в `brief.md` — ссылка на `tables/diplomacy.csv` (после задачи 03; до этого — без блока). Интерпретации NPC (`state`, `state_detail`) убрать. Готово: `brief.md` не содержит «Союзники»/«Враги» и значений `dead_or_captured`, если решено удалить; `tools/diff.py` ожидания по `state_code` остаются. Не начат.
4. Регрессия и доказательство. Один табличный тест публикации: на фикстурной публикации нет ключей `standing`, `*_calculated`, слов блока «Союзники»; ловит возврат удалённой аналитики через новые модули. Остальные тесты — только правка существующих. Готово: `python -m unittest discover -s tests`, `python tests/run_evidence.py`, `python -m tools.campaign check-schemas`, `python tests/check_seed.py`, `git diff --check` проходят; список изменённых/удалённых тестов с причиной приложен к `result.md`. Не начат.

Зависимости: независима от 02; задача 03 использует общую схему и `brief.md`. Риски: старые снимки в `data/snapshots/` (50 шт.) должны проходить `check-schemas`/diff; тесты на `classify_standing` удаляются, а не переписываются под «сырые» значения.
