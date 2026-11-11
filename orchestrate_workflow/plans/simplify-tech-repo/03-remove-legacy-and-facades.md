# 03-remove-legacy-and-facades

## Цель

Удалить из кодовой базы неиспользуемые модули-фасады (`world_builder.py`, `screenshot_annotator.py`, `zipnames.py`), адаптер `session_diff.py`, устаревшие промпты в `prompts/`, устаревшие справочные файлы в `references/`, файл `GEMINI.md`. Удалить исчерпывающий перечень завершённых каталогов планов в `orchestrate_workflow/plans/` по прямому указанию пользователя оставить только действующий план `simplify-tech-repo/` и файл `method.md`. Очистить проектную документацию и комментарии кода от упоминаний понятий веток кампании, `[ход]`, блокнотов и Gemini Notebook.

## Решения

- 2026-11-11. Задание пользователя: удалить неиспользуемые фасады `tools/world_builder.py`, `tools/screenshot_annotator.py`, `tools/zipnames.py`.
- 2026-11-11. Задание пользователя: функцию `real_name` (декодирование ZipInfo с эвристикой кодировок) перенести из `tools/zipnames.py` непосредственно в `tools/saves.py`.
- 2026-11-11. Задание пользователя: удалить адаптер `tools/session_diff.py`; единственный импорт `import session_diff` в `tools/session_export.py` перевести на `tools.diff` (`from tools import diff as session_diff`).
- 2026-11-11. Задание пользователя: обновить список `ENGINE_FILES` в `tools/converter_worker.py`, исключив `tools/session_diff.py` и `tools/zipnames.py`.
- 2026-11-11. Задание пользователя: удалить файлы промптов `prompts/01-START-AGENT.md`, `prompts/02-EXECUTE-APPROVED.md`, весь каталог `references/`, файл `GEMINI.md`.
- 2026-11-11. Задание пользователя: обновить `tests/check_seed.py`, исключив удалённые файлы из списка `required`.
- 2026-11-11. По замечанию ревью (P2): зафиксировать исчерпывающий перечень удаляемых каталогов планов в `orchestrate_workflow/plans/`:
  1. `orchestrate_workflow/plans/llm-payload-optimization/`
  2. `orchestrate_workflow/plans/mvp-pipeline/`
  3. `orchestrate_workflow/plans/native-pipeline/`
  4. `orchestrate_workflow/plans/screenshot-hud-index/`
  5. `orchestrate_workflow/plans/technical-exports/`
  6. `orchestrate_workflow/plans/unique-entities-registry/`
  7. `orchestrate_workflow/plans/visual-feed-annotation/`
  Основание: прямое требование пользователя в задаче («Завершённые планы в orchestrate_workflow/plans/; orchestrate_workflow/method.md остаётся»). Каталоги `llm-payload-optimization` и `technical-exports`, несмотря на исторические промежуточные отметки в их 00_summary, полностью завершены либо замещены новыми контрактами и подлежат удалению, оставляя в `orchestrate_workflow/plans/` исключительно действующий план `simplify-tech-repo/` и файл `orchestrate_workflow/method.md`.
- 2026-11-11. Задание пользователя: вычистить упоминания веток кампании (концепция одной линии кампании по ADR `campaign-line.md`), процедурной метки `[ход]`, блокнотов и Gemini Notebook в `README.md`, `AGENTS.md`, `docs/` и комментариях кода.

## Этапы задачи

1. Инлайн `real_name` и удаление фасадов: перенести `real_name` в `tools/saves.py`; удалить `tools/zipnames.py`, `tools/world_builder.py`, `tools/screenshot_annotator.py`; в `tools/session_export.py` заменить импорт на `from tools import diff as session_diff`; удалить `tools/session_diff.py`; в `tools/converter_worker.py` обновить `ENGINE_FILES`. Готово: `tools/saves.py` и `tools/session_export.py` работают без ошибок импорта; нативный движок проходит проверку контрольных сумм. Не начат.
2. Удаление устаревших промптов, ссылок и каталогов планов:
   - удалить `prompts/01-START-AGENT.md`, `prompts/02-EXECUTE-APPROVED.md` (и пустой каталог `prompts/`);
   - удалить каталог `references/`;
   - удалить `GEMINI.md`;
   - удалить 7 каталогов планов: `llm-payload-optimization/`, `mvp-pipeline/`, `native-pipeline/`, `screenshot-hud-index/`, `technical-exports/`, `unique-entities-registry/`, `visual-feed-annotation/`;
   - сохранить файл привязки `orchestrate_workflow/method.md`;
   - обновить список `required` в `tests/check_seed.py`.
   Готово: `python tests/check_seed.py` завершается с кодом 0; в `orchestrate_workflow/plans/` остаётся только каталог `simplify-tech-repo/`. Не начат.
3. Очистка кода и комментариев от легаси-терминологии: удалить устаревшие упоминания веток кампании, `[ход]`, блокнотов и Gemini Notebook из комментариев в `tools/`, `docs/` и `README.md`. Готово: текстовый поиск `git grep -i "блокнот"` и `git grep -i "gemini notebook"` по репозиторию не возвращает совпадений в активном коде. Не начат.
