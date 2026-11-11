# 04-compact-readme-and-contract-docs

## Цель

Сократить `README.md` до компактного руководства объёмом в одну страницу, содержащего только суть работы конвертера, инструкцию по запуску выгрузки и доставки, а также перечень артефактов `published/` со ссылкой на `docs/data-contract.md`. Сократить или слить документ `docs/chatgpt-interface.md`, устранив смысловое дублирование. Актуализировать контракт данных `docs/data-contract.md` (удаление упоминаний PDF и ссылки на `references/observed-export.md`), документацию скриншотов `docs/screenshots.md` (удаление `screenshots_latest.pdf` и Google Диска), маршрутизацию `orchestrate_workflow/method.md` (удаление ссылки на `observed-export.md`) и проектный контракт `AGENTS.md`.

## Решения

- 2026-11-11. Задание пользователя: `README.md` на одну страницу — назначение конвертера (чтение сейва Kenshi, сборка `published/`, отправка в `asaf1n/Kenshi-Compain`), каноническая команда запуска выгрузки и доставки, список артефактов `published/` со ссылкой на `docs/data-contract.md`.
- 2026-11-11. Задание пользователя: `docs/chatgpt-interface.md` сократить или слить, устранив дублирование с `README.md` и `docs/data-contract.md`, оставив только специфику потребления данных внешними LLM-ассистентами (read-only доступ к `published/`, правила чтения таблиц и реестров).
- 2026-11-11. По замечанию ревью (P2): актуализировать `docs/data-contract.md`:
  - в строке 5 удалить ссылку на удаляемый файл `references/observed-export.md`;
  - в разделе описания скриншотов (строки 146, 148, 152) удалить упоминания `screenshots_latest.pdf`, обещание доступности кадров через PDF и упоминания копий на Google Диске.
- 2026-11-11. По замечанию ревью (P2): актуализировать `docs/screenshots.md`:
  - в строке 55 удалить строку `├── screenshots_latest.pdf # Вне git: кадры дельты с текстовой шапкой для Google Диска` из схемы каталога публикации `screenshots/`;
  - привести состав описанных артефактов скриншотов в полное соответствие с отсутствием PDF и Google Диска.
- 2026-11-11. По замечанию ревью (P2): в сохраняемом файле маршрутизации `orchestrate_workflow/method.md` удалить строку со ссылкой на `references/observed-export.md` из таблицы предметных источников.
- 2026-11-11. Актуализация `AGENTS.md`: убрать строку синхронизации с Google Диском (`tools/gdrive_sync.py`, `docs/gdrive-sync.md`) из таблицы неподвижных правил; исключить из пункта 9 правила про Google Диск, нативные файлы Docs/Sheets и `Save-Packet.ps1`; исключить ссылки на удалённый `GEMINI.md`.
- 2026-11-11. Актуализация сопутствующей документации: очистить `docs/architecture.md`, `docs/requirements.md` и `docs/acceptance.md` от ссылок на Google Диск, PDF и приём пакетов `inbox`.

## Этапы задачи

1. Компактизация `README.md`: переписать `README.md` в лаконичной форме на 1 страницу без устаревших разделов о Google Диске, PDF, блокнотах Gemini и ручных многоступенчатых вызовах. Готово: `README.md` описывает цель, запуск через `.\Initialize-Pipeline.ps1` / `python -m tools.campaign pipeline`, доставку через `deliver-companion` и перечень файлов `published/` со ссылкой на `docs/data-contract.md`. Не начат.
2. Сокращение `docs/chatgpt-interface.md`: убрать из документа раздел о синхронизации с Google Диском и блокнотах Gemini, исключить дублирование описания артефактов, сохранив только прикладные правила интерпретации для моделей. Готово: `docs/chatgpt-interface.md` не содержит упоминаний Google Диска/Gemini и не дублирует структуру release bundle из `README.md`. Не начат.
3. Актуализация SSOT-контрактов, документации скриншотов и маршрутизации (`docs/data-contract.md`, `docs/screenshots.md`, `orchestrate_workflow/method.md`, `AGENTS.md`):
   - в `docs/data-contract.md` удалить ссылку на `references/observed-export.md`, удалить описание `screenshots_latest.pdf` и упоминания Google Диска;
   - в `docs/screenshots.md` удалить строку 55 (`screenshots_latest.pdf`) и привести описание состава публикации к актуальному;
   - в `orchestrate_workflow/method.md` удалить строку `references/observed-export.md`;
   - в `AGENTS.md` удалить строку Google Диска из таблицы правил, очистить пункт 9 от правил Диска/нативных файлов, убрать упоминания `GEMINI.md`;
   - в `docs/architecture.md`, `docs/acceptance.md`, `docs/requirements.md` убрать ссылки на Google Диск и PDF.
   Готово: `git diff --check` проходит чисто; в репозитории отсутствуют битые ссылки на `references/` и `docs/gdrive-sync.md`. Не начат.
