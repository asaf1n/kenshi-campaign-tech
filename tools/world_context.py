"""Обогащение производственного и геополитического контекста (станки, здания, NPC).

Связывает target_handle в jobs и carrying с реальными именами зданий,
оборудования и персонажей. Вычисляет складской баланс по базам и локациям.
Детализирует статус уникальных NPC (пленение, причастность отряда игрока).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Sequence


def _clean_handle_tuple(h: Sequence[int | None] | None) -> tuple[int | None, ...] | None:
    if not h or len(h) < 5:
        return None
    # Безадресные глобальные задачи имеют TYPE=11
    if h[0] == 11 or all(v in (0, None) for v in h[1:]):
        return None
    return tuple(h[:5])


def aggregate_storage_by_location(buildings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Агрегирует складские запасы базы игрока по городам / зонам."""
    location_counts: dict[str, Counter[str]] = defaultdict(Counter)
    buildings_per_loc: Counter[str] = Counter()

    def process_building(b: dict[str, Any], parent_loc: str | None = None) -> None:
        loc = b.get("town") or b.get("zone") or parent_loc or "Общая база"
        buildings_per_loc[loc] += 1

        # Извлекаем предметы из storage_items (v11) или storage (v10)
        items = b.get("storage_items") or []
        for it in items:
            name = it.get("name") or it.get("base_data_sid") or it.get("item_sid") or "Неизвестный предмет"
            qty = it.get("quantity")
            qty_val = int(qty) if isinstance(qty, (int, float)) and qty > 0 else 1
            location_counts[loc][name] += qty_val

        for row in b.get("storage") or []:
            name = row.get("name") or "Неизвестный предмет"
            qty = row.get("quantity")
            qty_val = int(qty) if isinstance(qty, (int, float)) and qty > 0 else 1
            # Если уже учтено через storage_items, не дублируем
            if not items:
                location_counts[loc][name] += qty_val

        for sub in b.get("interior", []):
            process_building(sub, loc)

    for b in buildings:
        process_building(b)

    result: dict[str, dict[str, Any]] = {}
    for loc in sorted(buildings_per_loc.keys()):
        cnt = location_counts[loc]
        total_items = sum(cnt.values())
        top_items = [{"name": name, "quantity": qty} for name, qty in cnt.most_common()]
        result[loc] = {
            "buildings_count": buildings_per_loc[loc],
            "total_items_count": total_items,
            "items_summary": top_items,
        }
    return result


def enrich_world_and_production_context(
    characters: list[dict[str, Any]],
    property_data: dict[str, Any] | None,
    unique_npcs: list[dict[str, Any]] | None,
) -> None:
    """Обогащает jobs, carrying, unique_npcs и рассчитывает складской баланс по базам."""
    handle_map: dict[tuple[int | None, ...], str] = {}

    # 1. Индексация персонажей отряда
    for ch in characters:
        h = _clean_handle_tuple(ch.get("handle"))
        if h:
            handle_map[h] = ch.get("name") or ch.get("id") or "Персонаж"

    # 2. Индексация уникальных NPC мира
    for u in unique_npcs or []:
        uh = _clean_handle_tuple(u.get("handle"))
        if uh:
            handle_map[uh] = u.get("name_ru") or u.get("name") or u.get("sid") or "NPC"

    # 3. Индексация зданий и оборудования
    buildings = (property_data or {}).get("buildings", []) if isinstance(property_data, dict) else []

    def index_building(b: dict[str, Any], parent_name: str | None = None) -> None:
        bh = _clean_handle_tuple(b.get("handle"))
        b_name = b.get("name") or b.get("template_name") or "Здание"
        if bh:
            if parent_name:
                handle_map[bh] = f"{b_name} ({parent_name})"
            else:
                handle_map[bh] = b_name

        for sub in b.get("interior", []):
            index_building(sub, b_name)

    for b in buildings:
        index_building(b)

    # 4. Индексация переносимых отрядом целей (для детализации пленников)
    carried_targets: dict[tuple[int | None, ...], str] = {}
    for ch in characters:
        c_info = ch.get("carrying")
        if isinstance(c_info, dict) and c_info.get("is_carrying"):
            th = _clean_handle_tuple(c_info.get("target_handle"))
            if th:
                carried_targets[th] = ch.get("name") or ch.get("id") or "Персонаж"

    # 5. Обогащение работ и переноски у персонажей
    for ch in characters:
        jobs = ch.get("jobs")
        if isinstance(jobs, dict):
            for t in jobs.get("primary", []) + jobs.get("temporary", []):
                th = _clean_handle_tuple(t.get("target_handle"))
                if th and th in handle_map:
                    t["target_name"] = handle_map[th]

        c_info = ch.get("carrying")
        if isinstance(c_info, dict):
            th = _clean_handle_tuple(c_info.get("target_handle"))
            if th and th in handle_map:
                c_info["target_name"] = handle_map[th]
            cb = _clean_handle_tuple(c_info.get("carried_by"))
            if cb and cb in handle_map:
                c_info["carried_by_name"] = handle_map[cb]

    # 6. Детализация уникальных NPC
    for u in unique_npcs or []:
        uh = _clean_handle_tuple(u.get("handle"))
        st_code = u.get("state_code")
        pl = u.get("player")

        if uh and uh in carried_targets:
            u["state_detail"] = "captured (carried by squad)"
            u["captured_by"] = carried_targets[uh]
        elif st_code == 2:
            u["state_detail"] = "dead_or_captured (player involved)" if pl else "dead_or_captured (world event)"
        elif st_code == 1:
            u["state_detail"] = "active_world"
        elif st_code == 0:
            u["state_detail"] = "unaffected"

    # 7. Агрегация складского баланса по локациям
    if isinstance(property_data, dict) and buildings:
        property_data["storage_by_location"] = aggregate_storage_by_location(buildings)
