# 05-drive-profiles

## Цель

`sync-drive` доставляет на Диск не зеркало, а явный набор источников для двух блокнотов с раздельными каталогами. Только Markdown, CSV, PDF. Ориентир ≤ 30 файлов на профиль (лимит Gemini Notebook — 50 источников).

## Решения (пользователь, 2026-11-05)

Профиль «Игровой блокнот»:
- `published/`: `brief.md` (без аналитических блоков), `changes.md`, `tables/*` (CSV и `index.md`), `screenshots/index.md`, `screenshots/feed.md`, `screenshots/screenshots_latest.pdf`. Отдельные `.jpg` не входят. `squad.md` не копируется, если `tables/` покрывают все его поля (включая jobs); расхождения перечислить.
- `Campaign/<кампания>/`: только `Kenshi_Хронология.md`, `Kenshi_Текущая_арка.md`, `Kenshi_Архетипы_и_билды.md`. `archive/` не копируется.
- `docs/`: `companion-guide.md`, `session-protocol.md`, `response-formatting.md`, `Kenshi_sources.md`, `Kenshi_RU_EN.md`; `docs/reference/*.md` кроме `drafts/`; `templates/template-session.md`.
- Не копируются: `README.md`, `prompts/`, `sites/`, `docs/chronicler-guide.md`.

Профиль «Блокнот летописи»:
- `Campaign/<кампания>/`: `Kenshi_Хронология.md`, `archive/` — `sessions/`, `Kenshi_Библия.md`, `novel/`.
- `docs/`: `chronicler-guide.md`, `Kenshi_sources.md`, `Kenshi_RU_EN.md`. Данные `published/` не нужны.

`tmp/inbox`, `tmp/checkpoints`, `tmp/processed` — служебные, в наборы не входят. Выпавшие из набора файлы удаляются с Диска; `file_id` остающихся сохраняется (`write_in_place`). Отчёт выводит число файлов по профилям.

## Этапы

1. Чтение `tools/gdrive_sync.py`, `docs/gdrive-sync.md`; определить, откуда берутся `Campaign/`, `docs/`, `templates/` (локальный клон Kenshi-Compain?) и текущую структуру каталогов на Диске. Готово: описание источников и конфигурации; если источник не найден локально — зафиксировать как ограничение. Не начат.
2. Конфигурация профилей (allowlist) и реализация: два профиля, раздельные каталоги, удаление выпавших файлов, счётчик по профилям. Проверка покрытия `squad.md` таблицами (перечень расхождений). Готово: `python -m unittest tests.test_gdrive_sync`; случай «выпавший файл удаляется, остающиеся сохраняют `file_id`»; повторная синхронизация ничего не пишет. Диск `G:` не подключён — реальный прогон «не запускался». Не начат.
