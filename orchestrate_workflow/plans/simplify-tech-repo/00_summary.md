# simplify-tech-repo: Упрощение репозитория kenshi-campaign-tech

Статус плана: завершён 2026-11-11
Вердикт независимого ревью: принято (orchestrate_workflow/simplify-tech-repo/review.md)

Цель плана: Упростить репозиторий kenshi-campaign-tech до единственной задачи — чтение сохранения Kenshi, сборка release bundle `published/` и односторонняя доставка в `asaf1n/Kenshi-Compain`. Полностью устранить приём пакетов и синхронизацию с Google Диском, генерацию PDF-артефактов, неиспользуемые фасады, устаревшие адаптеры, промпты и архивы планов при сохранении неизменными контрактов `published/`, схем в `contracts/` и структуры таблиц.

Открытая задача: отсутствует (все этапы завершены)

| Задача | Состояние очереди | Назначение |
|---|---|---|
| [01-remove-gdrive-and-packets](01-remove-gdrive-and-packets.md) | закрыта (коммит a20febe) | Удаление Google Диска, приёма пакетов, PDF-генератора и reportlab, отвязка screenshot_delta от GDRIVE_SESSION_DIR, очистка зависимых CLI-команд, конфигов и тестов |
| [02-brief-and-delta-cleanup](02-brief-and-delta-cleanup.md) | закрыта (коммит ba1920a) | Очистка brief.md от строки-указания «[gamedata_export]», замена текста «только PDF и Google Диск» в render_frames_table, очистка screenshots/index.md от ссылок на Диск |
| [03-remove-legacy-and-facades](03-remove-legacy-and-facades.md) | закрыта (коммит 157e1a2) | Удаление фасадов (world_builder, screenshot_annotator, zipnames с инлайном real_name в saves.py), адаптера session_diff, устаревших промптов, каталога references/, GEMINI.md и завершённых планов |
| [04-compact-readme-and-contract-docs](04-compact-readme-and-contract-docs.md) | закрыта (коммит 88cbf87) | Сокращение README.md до одной страницы, сжатие docs/chatgpt-interface.md, актуализация docs/data-contract.md, orchestrate_workflow/method.md и AGENTS.md |
| [05-end-to-end-verification](05-end-to-end-verification.md) | закрыта (подтверждено ревью) | Сквозная приёмочная верификация: сборка published/ из сейва окончательным кодом, валидация манифеста, доставка с push, сверка манифеста и 25 cleared локаций |
