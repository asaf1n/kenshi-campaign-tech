# Результат этапа 1.4: Вывод из эксплуатации внешнего воркера (исправленный)

## 1. Измененные и добавленные файлы
- `tools/config.py`: удалено поле `converter`, исключено чтение переменной окружения `KENSHI_CONVERTER_DIR`, метод `protected()` очищен от ссылки на внешний конвертер.
- `tools/converter_worker.py`: переведен на полностью нативный движок `tools.session_export`, `tools.gamedb`, `tools.kenshi_gd`, `tools.saves`. Строгие проверки целостности (`consumed > 0`, тип записи 56, проверка всех активных файлов `.platoon`). Устранен тестовый крючок `_test_attempt`.
- `tools/extraction.py`: заменена функция `converter_hashes` на `engine_hashes()`, отслеживающую хеши всех 12 модулей нативного движка (`converter_worker.py`, `export_platoons.py`, `gamedb.py`, `itemstats.py`, `kenshi_gd.py`, `player_assets.py`, `prosthetics.py`, `save_reader.py`, `saves.py`, `session_diff.py`, `session_export.py`, `zipnames.py`). Убран параметр `test_attempt`.
- `tools/itemstats.py`, `tools/prosthetics.py`, `tools/player_assets.py`, `tools/export_platoons.py`, `tools/saves.py`, `tools/session_export.py`, `tools/zipnames.py`: интегрированы непосредственно в `tools/`, обеспечивая 100% совместимость и паритет с контрактом `kenshi-session/10` (расчет урона оружия, сопротивлений и покрытия брони, характеристик протезов, статусов персонажей, категорий припасов и дерева исследований).
- `tools/gamedb.py`: динамическое разрешение путей `KENSHI_DIR` и `KENSHI_WORKSHOP_DIR` через `os.environ`.
- `tools/kenshi_gd.py`: расширена поддержка `read_file` для `fileType in (15, 16)`.
- `tests/test_extract.py`: проверка `install_write_guard` перенесена в изолированный субпроцесс, синхронизированы хеши нативного движка `ENGINE_FILES`.
- `tests/check_converter.py`, `tests/check_e2e_real.py`, `tests/test_locations.py`, `tests/test_publish.py`: переведены на нативные интерфейсы и изолированы от глобального состояния `data/index.json`.
- `published/`: рабочее дерево сохранено чистым (`git restore published/`), 0 непредусмотренных изменений.

## 2. Верификация
1. `python -m unittest discover -s tests -v`: 66/66 тестов успешно пройдены (OK, 40.8s).
2. `python tests/run_evidence.py`:
   - `seed`: OK
   - `schemas`: OK (8 проверенных примеров)
   - `build-world`: OK (261 локация)
   - `world_reference`: OK
   - `save`: OK (живой autosave0 Kenshi)
   - `locations`: OK
   - `branches`: OK
   - `timeline`: OK
   - `converter`: OK (на синтетическом фикстурном сейве)
   - `export`: OK
   - `e2e_real`: OK (на живых сейвах autosave0 и autosave1 Kenshi)
   - `unit`: OK (полный прогон discover)
   - `diff`: OK (`git diff --check` чист)
   Все доказательства собраны успешно.
