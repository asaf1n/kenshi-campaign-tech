# 05-end-to-end-verification

## Цель

Провести полную сквозную верификацию упрощённого репозитория: удостовериться в успешном прохождении всего набора unit-тестов, схем контрактов и smoke-тестов семян; выполнить реальную сборку `published/` из сохранения Kenshi окончательной версией конвейера с валидацией выпуска; произвести доставку release bundle в `asaf1n/Kenshi-Compain` с push, сверкой доставленного и удалённого `manifest.json` по идентификаторам и хешам, а также проверкой `location_registry.json` на наличие ровно 25 локаций со статусом `cleared`.

## Решения

- 2026-11-11. Задание пользователя: обязательные проверочные команды:
  1. `python -m unittest discover -s tests` — все тесты проходят без ошибок и сбоев.
  2. `python -m tools.campaign check-schemas` — валидация всех схем и примеров контрактов.
  3. `python tests/check_seed.py` — проверка целостности стартового комплекта файлов.
  4. `python -m tools.campaign deliver-companion --push` — доставка выгрузки в игровой репозиторий с отправкой коммита в GitHub.
  5. Проверка `location_registry.json` на 25 cleared локаций в доставленном бандле.
- 2026-11-11. По замечанию ревью (P1): предотвратить доставку старого выпуска без прогона обновлённого кода — перед доставкой обязательно выполнить полный запуск конвейера сборки `published/` из актуального сохранения Kenshi (`.\Initialize-Pipeline.ps1` или `python -m tools.campaign pipeline`), подтвердить статус валидации выпуска `validation.status == "passed"` и проверить отсутствие артефактов Google Диска и PDF в `published/`.
- 2026-11-11. По замечанию ревью (P1): после выполнения `deliver-companion --push` произвести сверку доставленного в клон `Kenshi-Compain` манифеста и удалённой ветки GitHub по `snapshot_id`, `parent_snapshot_id`, `campaign_id`, `source_sha256`, `world_ref_id` и `generated_at`.
- 2026-11-11. Инвариант контрактов: схемы в `contracts/`, форматы `published/` и имена таблиц `tables/` остаются строго неизменными для совместимости с внешним компаньоном `asaf1n/Kenshi-Compain`.

## Этапы задачи

1. Полный регрессионный прогон unit-тестов и проверка схем: выполнить `python -m unittest discover -s tests`, `python -m tools.campaign check-schemas` и `python tests/check_seed.py`. Готово: все три команды завершаются с exit code 0; в тестах 0 failures и 0 errors. Не начат.
2. Сборка выпуска `published/` из сохранения окончательной версией кода: выполнить запуск конвейера из актуального сохранения Kenshi (через `.\Initialize-Pipeline.ps1` либо `python -m tools.campaign pipeline --source-kind save ...`); проверить формирование каталога `published/` и статус валидации в `published/manifest.json` (`validation.status == "passed"`); убедиться в отсутствии `screenshots_latest.pdf` в `published/screenshots/`. Готово: конвейер завершился с кодом 0; манифест валиден; выпуск собран обновлённым кодом. Не начат.
3. Сквозная доставка, сверка манифеста и верификация 25 cleared локаций:
   - запустить `python -m tools.campaign deliver-companion --push`;
   - сверить метаданные манифеста в клоне `Kenshi-Compain` (`published/manifest.json`) с локальным выпуском (`snapshot_id`, `source_sha256`, `generated_at`);
   - проверить артефакт `published/location_registry.json` в репозитории `Kenshi-Compain`: подтвердить наличие ровно 25 локаций с полем `cleared`.
   Готово: команда доставки завершилась со статусом `"status": "delivered"`, `"pushed": true`; манифесты идентичны; в `location_registry.json` подтверждены 25 зачищенных локаций. Не начат.
