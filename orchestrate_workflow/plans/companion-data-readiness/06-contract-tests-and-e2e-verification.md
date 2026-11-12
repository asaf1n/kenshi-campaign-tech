# 06-contract-tests-and-e2e-verification

## Цель

Синхронизировать документацию контракта по структуре полей `unique_entities`, откалибровать непокрытые классы предметов со статусом `unverified_class`, устранить нестабильные тесты `LIVE-SLOT` из основного тестового набора и выполнить сквозную приемочную верификацию всего конвейера с контрольным запуском на реальном сохранении.

## Решения

- 2026-11-12. Аудит audit-2026-11.md (Находка 6) и ревью:
  1) в `docs/data-contract.md` явно зафиксировать архитектурное различие: в `unique_entities_registry.json` поля состояния и локации вложены в `dynamic.state_code` и `locations[].resolution`, а в плоской таблице `tables/unique_entities.csv` эти поля проецируются на верхний уровень через `resolve_primary_location`;
  2) зафиксировать строгое правило верификации: статус `verified` для уникальных сущностей присваивается только после объективного подтверждения по скриншотам (наличие кадра в `screenshots/` с персонажем или предметом), а не автоматически по факту упоминания в тексте или записях;
  3) актуализировать схему `contracts/unique-entities-registry.schema.json`.
- 2026-11-12. Аудит audit-2026-11.md (Находка 7): калибровка 64 предметов со статусом `unverified_class`:
  1) протезы рук: добавить множители атрибутов в `tools/prosthetics.py` для подтверждения классов рук (КЛР, вор, промгрузчик);
  2) оружие ближнего боя Катунского Кулибина, Скелетов-кузнецов и Древних: уточнить формулу округления веса в FCS в `tools/itemstats.py`;
  3) ткань и легкая броня: калибровать резисты и покрытие в `tools/itemstats.py`.
- 2026-11-12. Аудит audit-2026-11.md (Находка 10): стабилизировать 4 теста `LIVE-SLOT`:
  1) `tests/test_base_production_extraction.py:240`;
  2) `tests/test_character_jobs_and_carrying.py:266`;
  3) `tests/test_character_jobs_and_carrying.py:309`;
  4) `tests/test_diplomacy_extraction.py:166`.
  Перевести их на статичные фикстуры сейвов либо перенести в специализированный модуль `tests/live/` с запуском по явному флагу, очистив регулярный прогон `python -m unittest discover -s tests` от пропусков (`skipped=0` для основного набора) и обновив регулярное выражение в `tests/run_evidence.py`.
- 2026-11-12. Регламент приёмки: провести полную сквозную верификацию:
  1) `python -m unittest discover -s tests` (все тесты проходят);
  2) `python tests/run_evidence.py` (evidence собран без сбоев);
  3) `python -m tools.campaign check-schemas` (все схемы валидны);
  4) `git diff --check` (нет пробельных ошибок);
  5) сквозной запуск `Initialize-Pipeline.ps1` на реальном сохранении с верификацией статуса `validation.status=passed` и доставкой в `Kenshi-Compain`.

## Этапы задачи

1. Синхронизация `docs/data-contract.md` и схемы `contracts/unique-entities-registry.schema.json`. Готово: документация точно описывает вложенность полей в JSON и плоскую проекцию в CSV; `python -m tools.campaign check-schemas` подтверждает валидность. Не начат.
2. Калибровка карточек предметов `unverified_class` в `tools/itemstats.py` и `tools/prosthetics.py`. Готово: число непокрытых предметов сокращено; протезы рук и оружие получают верифицированные статы `calc_*`; тесты `tests.test_itemstats` и `tests.test_prosthetics` успешно проходят. Не начат.
3. Стабилизация 4 LIVE-SLOT тестов и очистка отчета `unittest discover`. Готово: 4 теста переведены на статичные фикстуры или вынесены в `tests/live/`; запуск `python -m unittest discover -s tests` не содержит пропусков `LIVE-SLOT`; `tests/run_evidence.py` успешно завершается с кодом 0. Не начат.
4. Сквозная приемочная верификация конвейера и интеграционный прогон `Initialize-Pipeline.ps1`. Готово: выполнен полный сквозной запуск конвейера; артефакты `published/` сгенерированы в обновленном компактном виде; статус валидации `passed`; доставка в `Kenshi-Compain` выполнена успешно. Не начат.
