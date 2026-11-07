"""Публичные проекции данных выпуска: очищенный current.json и справочник мира без служебных полей.

Исходные снимки в data/snapshots/ и расчёт разниц сохраняют полный набор полей; очистка выполняется
только при формировании публичных файлов (published/extended/current.json, published/world_reference.json).
"""
from __future__ import annotations

from typing import Any

DROPPED_KEYS = frozenset({"raw", "hidden_stats_raw"})
WORLD_DROPPED_KEYS = frozenset({"regions_map", "source_hashes"})


def public_projection(value: Any) -> Any:
    """Копия данных без `raw`, `hidden_stats_raw` и навыков со значением строго 1.0 (отсутствующий навык = 1.0)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in DROPPED_KEYS:
                continue
            projected = public_projection(item)
            if key == "skills_base" and isinstance(projected, dict):
                projected = {name: level for name, level in projected.items() if isinstance(level, bool) or level != 1.0}
            out[key] = projected
        return out
    if isinstance(value, list):
        return [public_projection(item) for item in value]
    return value


def public_world_reference(world_ref: dict[str, Any]) -> dict[str, Any]:
    """Публикуемый справочник мира без растра регионов и контрольных сумм исходников (полный файл остаётся в data/world/)."""
    return {key: value for key, value in world_ref.items() if key not in WORLD_DROPPED_KEYS}
