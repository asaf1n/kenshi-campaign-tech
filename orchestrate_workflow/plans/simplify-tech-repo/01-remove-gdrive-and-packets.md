# 01-remove-gdrive-and-packets

## Цель

Полностью удалить из репозитория подсистему синхронизации с Google Диском, приём пакетов `inbox`, вспомогательную авторизацию Google API, скрипт `Save-Packet.ps1` и генератор `screenshot_pdf.py`. Исключить внешнюю зависимость `reportlab` из конвейера и скрипта запуска. Устранить ВСЕ обращения к `GDRIVE_SESSION_DIR` в `tools/screenshot_delta.py` (и в `drive_path`, и в `render_index_md`) до удаления констант Диска из `tools/config.py`, исключив `NameError` и сохранив канонический путь кадра `published/screenshots/...`. Очистить зависимые команды в `tools/campaign.py`, вызовы в `tools/publisher.py`, конфигурацию в `tools/config.py`, а также зависимые тесты в `tests/test_campaign_check.py`, `tests/test_git_sync.py`, `tests/test_screenshot_delta.py` и `tests/test_screenshot_annotations.py`, сохранив доставку в `Kenshi-Compain` (`tools/deliver_companion.py`, `tools/git_sync.py`) полностью работоспособной.

## Решения

- 2026-11-11. Задание пользователя: удалить файлы `tools/gdrive_sync.py`, `tools/gdrive_native.py`, `tools/gdrive_api.py`, `scripts/gdrive_auth.py`, `Save-Packet.ps1`, `tools/screenshot_pdf.py`, `docs/gdrive-sync.md`.
- 2026-11-11. Задание пользователя: удалить CLI-команды `sync-drive`, `gdrive-export`, `ingest-inbox` и флаг `--sync-drive` в `tools/campaign.py`.
- 2026-11-11. Задание пользователя: удалить вызов `screenshot_pdf` в `tools/publisher.py` (артефакт `screenshots_latest.pdf` исключён из сборки выпуска).
- 2026-11-11. По замечанию ревью (P1.1): устранить ВСЕ обращения к `GDRIVE_SESSION_DIR` в `tools/screenshot_delta.py` на этапе 01 до удаления констант из `tools/config.py`, предотвращая `NameError` при импорте модуля:
  1) строка 166: заменить обращение к `GDRIVE_SESSION_DIR` на строковый литерал `"published"`, сохранив путь `drive_path = f"published/screenshots/{name}"`;
  2) строка 215: удалить строку `f"- **Пакет для просмотра на Google Диске:** ..."` из функции `render_index_md`;
  3) удалить строку импорта `from tools.config import GDRIVE_SESSION_DIR`.
- 2026-11-11. По замечанию ревью (P2): в `Initialize-Pipeline.ps1` удалить проверку наличия библиотеки `reportlab` (строки 117-119), а также передачу аргумента `--sync-drive` и разбор `$driveLine`.
- 2026-11-11. По замечанию ревью (P2): в `tests/test_screenshot_delta.py` удалить импорт `build_screenshots_pdf`, проверки наличия `screenshots_latest.pdf` и тестовый метод `test_pdf_skips_truncated_frame`.
- 2026-11-11. По замечанию ревью (P2): в `tests/test_screenshot_annotations.py` (строка 338) удалить ожидание `"screenshots_latest.pdf"` в списке опубликованных файлов скриншотов (`names`).
- 2026-11-11. Задание пользователя: удалить тесты `tests/test_gdrive_sync.py`, `tests/test_gdrive_native.py`, `tests/test_gdrive_api.py`.
- 2026-11-11. Задание пользователя: удалить переменную `KENSHI_GDRIVE_DIR`, константы каталогов Диска (`GDRIVE_*`) и функцию `find_gdrive_root` в `tools/config.py`.
- 2026-11-11. Задание пользователя: очистить `tests/test_campaign_check.py` (убрать `--sync-drive` и mock `sync_drive`), `tests/test_git_sync.py` (убрать тест `ingest_inbox`, вызов `ensure_buffer` и фикстуру `self.drive`).
- 2026-11-11. Архитектурный инвариант: доставка release bundle в `asaf1n/Kenshi-Compain` (`tools/deliver_companion.py`, `tools/git_sync.py`) является единственным внешним каналом публикации и сохраняется без изменений.

## Этапы задачи

1. Полная отвязка screenshot_delta и удаление модулей Google Диска, скриптов и тестов синхронизации:
   - в `tools/screenshot_delta.py` устранить оба обращения к `GDRIVE_SESSION_DIR`: в `drive_path` зафиксировать путь `f"published/screenshots/{name}"`, в `render_index_md` удалить строку о пакете на Google Диске, удалить импорт `from tools.config import GDRIVE_SESSION_DIR`;
   - удалить файлы `tools/gdrive_sync.py`, `tools/gdrive_native.py`, `tools/gdrive_api.py`, `scripts/gdrive_auth.py`, `Save-Packet.ps1`, `tools/screenshot_pdf.py`, `docs/gdrive-sync.md`, `tests/test_gdrive_sync.py`, `tests/test_gdrive_native.py`, `tests/test_gdrive_api.py`.
   Готово: модуль `tools.screenshot_delta` импортируется и выполняется без `GDRIVE_SESSION_DIR` и `NameError`; перечисленные 10 файлов удалены из репозитория. Не начат.
2. Очистка конфигурации и удаление PDF из сборки публикации: в `tools/config.py` удалить константы `GDRIVE_*`, функцию `find_gdrive_root` и атрибут `gdrive_root` у `Config`; в `.env.example` удалить строку `KENSHI_GDRIVE_DIR`; в `tools/publisher.py` удалить сборку PDF (`pdf_frames`, импорт `build_screenshots_pdf`, вызов копирования PDF). Готово: `tools/config.py` не содержит ссылок на Google Диск; `tools/publisher.py` не создаёт артефакт `screenshots_latest.pdf`. Не начат.
3. Очистка CLI и скрипта запуска (включая reportlab): в `tools/campaign.py` удалить команды `sync-drive`, `gdrive-export`, `ingest-inbox`, аргумент `--sync-drive` у подкоманды `pipeline` и соответствующие вызовы; в `Initialize-Pipeline.ps1` удалить проверку библиотеки `reportlab`, передачу `--sync-drive` и разбор статуса `$driveLine`. Готово: `python -m tools.campaign --help` и `pipeline --help` не содержат опций Диска; `Initialize-Pipeline.ps1` запускается без требования `reportlab`. Не начат.
4. Очистка зависимых тестов от Google Диска и PDF:
   - в `tests/test_campaign_check.py` удалить аргумент `--sync-drive` и mock `tools.gdrive_sync.sync_drive`;
   - в `tests/test_git_sync.py` удалить импорт `ensure_buffer`, `ingest_inbox`, строку `self.drive = ...` и тестовый метод `test_ingest_requires_main_and_recovers_after_remote_moved`;
   - в `tests/test_screenshot_delta.py` удалить импорт `build_screenshots_pdf`, проверки наличия `screenshots_latest.pdf` и тестовый метод `test_pdf_skips_truncated_frame`;
   - в `tests/test_screenshot_annotations.py` удалить `"screenshots_latest.pdf"` из проверяемого списка артефактов `names`.
   Готово: тесты `tests.test_campaign_check`, `tests.test_git_sync`, `tests.test_screenshot_delta` и `tests.test_screenshot_annotations` успешно проходят (`Ran N tests ... OK`). Не начат.
