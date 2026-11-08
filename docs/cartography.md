# Картография

Производные геометрии мира для просмотрщика Kenshi-Compain (`sites/map/index.html`). Код: `tools/cartography.py`, тесты: `tests/test_cartography.py`.

## Входы и артефакты

| Объект | Расположение | Правило |
|---|---|---|
| Растр `areasmap.tga` (24-битный RGB, цвет = индекс `BIOME_GROUP`) | `data/world/<world_ref_id>.json`, `regions_map.rle` | Локальный, вне git. Не встраивается ни в один репозиторий |
| Контуры регионов | `assets/cartography/regions_map.svg` | Производный; хеш закреплён в `GEOMETRY_SVG_SHA256`. Идентификаторов регионов нет: регион задан `<title>Имя (name_en)</title>` |
| Таблица соседства | `assets/cartography/regions_adjacency.json` | Производная от растра; хеши исходного растра, декодированных ячеек и таблицы; алгоритм `rle-four-neighbors/1` (4-связность, `NONE` исключён) |

Сопоставление регионов идёт по `name_en`/`sid`, не по русским строкам. `The Iron Trail` отображается как «Железный шельф».

## Коридоры

Цепочка — последовательность регионов. Шаг имеет статус `border` (общая граница в растре) или `gap`. Цепочка не достраивается, линий между регионами нет, оценок проходимости и безопасности нет. Глава XI: Пойма → Гиблые земли, вариант А через Возрождение, вариант Б через Руку Окрана (Край Мира).

## Команды

```powershell
python -m tools.cartography --verify-geometry            # фикстурный режим: хеши SVG и таблицы
python -m tools.cartography --verify-geometry --local    # локальный режим: пересчёт по data/world, world_source_verified=true
python -m tools.cartography --build-adjacency --world-source data/world/<world_ref_id>.json
python -m tools.cartography --svg tmp/map.svg            # экспорт текущего published/
```

## Доставка и совместимость просмотрщика

`deliver-companion` копирует оба артефакта в `docs/reference/` игрового репозитория в коммите выгрузки. Перед записью проверяется `sites/map/index.html` клона (`check_viewer_compat`): граф, контуры, цепочки и `world_ref_id` просмотрщика совпадают с артефактами, растра и формулировок о проходимости нет, по умолчанию загружается `../../published/`, замена выпуска требует `confirm`. Несовместимый просмотрщик блокирует запись артефактов и даёт `status=failed`; отсутствие просмотрщика (ветка не влита) ошибкой не является.

## Проверка

`python -m unittest tests.test_cartography`. Фикстурный режим (CI) не требует `data/world` и `published/`; локальные классы пропускаются без них.
