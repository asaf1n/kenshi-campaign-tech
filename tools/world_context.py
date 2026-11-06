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


ZONE_KEY_FORMAT: str = "зона {zone} (поселение не определено)"


def _building_places(locations: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """building_key ('<зона>:<id>') -> запись locations.json."""
    return {b["building_key"]: b for b in (locations or {}).get("buildings", []) if b.get("building_key")}


def zone_settlements(locations: dict[str, Any] | None, world_ref: dict[str, Any] | None) -> dict[str, str]:
    """Зона карты -> поселение, если в зоне ровно одно название: из зданий и персонажей in_location (locations.json)
    и из локаций справочника мира (поле zone). Зоны с несколькими названиями не разрешаются."""
    names: dict[str, set[str]] = defaultdict(set)
    for item in [*(locations or {}).get("buildings", []), *(locations or {}).get("characters", [])]:
        loc = item.get("location")
        if item.get("zone") and loc:
            names[str(item["zone"])].add(loc.get("name_ru") or loc.get("name_en"))
    for loc in (world_ref or {}).get("locations", []):
        if loc.get("zone"):
            names[str(loc["zone"])].add(loc.get("name_ru") or loc.get("name_en"))
    return {z: next(iter(n)) for z, n in names.items() if len(n) == 1 and None not in n}


def _settlement(b: dict[str, Any], zone: str | None, places: dict[str, dict[str, Any]],
                parent: tuple[str | None, str] | None,
                zone_places: dict[str, str] | None = None) -> tuple[str | None, str]:
    """Название поселения и способ определения: in_location > town сейва > near_location > поселение родителя > zone_location > зона."""
    place = places.get(f"{zone}:{b.get('id')}") if zone else None
    loc = (place or {}).get("location")
    if loc:
        return loc.get("name_ru") or loc.get("name_en"), "in_location"
    if b.get("town"):
        return b["town"], "town"
    nearby = (place or {}).get("nearby_locations") or []
    if nearby:
        return nearby[0].get("name_ru") or nearby[0].get("name_en"), "near_location"
    if parent and parent[0]:
        return parent
    if zone and (zone_places or {}).get(zone):
        return zone_places[zone], "zone_location"
    return None, "zone"


def _stat_value(game_index: dict[str, list[Any]] | None, sid: str | None) -> tuple[Any, str | None]:
    stats = ((game_index or {}).get(sid or "") or [None] * 5)[4] or {}
    return stats.get("value"), stats.get("value_source")


def storage_rows(
    buildings: list[dict[str, Any]],
    locations: dict[str, Any] | None = None,
    game_index: dict[str, list[Any]] | None = None,
    zone_places: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Строки склада «поселение, здание, контейнер, предмет, количество, базовая цена» и список всех зданий.

    Имя предмета берётся из самой строки, затем из сводки `storage` здания (по sid), затем из индекса игры;
    sid остаётся только у нераспознанных предметов (`unresolved`). Базовая цена - `base_price` строки
    (записана при извлечении) или `value` индекса игры. Второй результат - здания с определённым способом
    привязки к поселению (в том числе пустые), чтобы здания без поселения были видны явно.
    """
    places = _building_places(locations)
    rows: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    def walk(b: dict[str, Any], parent: tuple[str | None, str] | None, parent_zone: str | None) -> None:
        zone = b.get("zone") or parent_zone
        settlement, how = _settlement(b, zone, places, parent, zone_places)
        label = b.get("name") or b.get("template_name") or b.get("template") or "Здание"
        nodes.append({"settlement": settlement, "settlement_status": how, "zone": zone,
                      "building_id": b.get("id"), "building": label})
        names = {r.get("sid"): r for r in b.get("storage") or [] if r.get("sid")}
        raw_items = b.get("storage_items") or []
        if not raw_items:  # v10: только сводка по зданию, без контейнеров
            raw_items = [{"base_data_sid": r.get("sid"), "name": r.get("name"), "quantity": r.get("quantity"),
                          "container": None} for r in b.get("storage") or []]
        for it in raw_items:
            sid = it.get("base_data_sid") or it.get("item_sid")
            entry = (game_index or {}).get(sid or "")
            name = it.get("name") or (names.get(sid) or {}).get("name") or (entry[1] or entry[0] if entry else None)
            price, price_src = it.get("base_price"), it.get("base_price_source")
            if price is None:
                price, price_src = _stat_value(game_index, sid)
            qty = it.get("quantity")
            container = it.get("container")
            rows.append({
                "settlement": settlement, "settlement_status": how, "zone": zone,
                "building_id": b.get("id"), "building": label,
                "container": container.split(">", 1)[1] if isinstance(container, str) and ">" in container else container,
                "item": name or sid or "Неизвестный предмет", "item_sid": sid, "unresolved": name is None,
                "quantity": int(qty) if isinstance(qty, (int, float)) and qty > 0 else 1,
                "base_price": price, "base_price_source": price_src,
            })
        for sub in b.get("interior") or []:
            walk(sub, (settlement, how) if settlement else parent, zone)

    for b in buildings:
        walk(b, None, None)
    rows.sort(key=lambda r: (r["settlement"] or "~", str(r["zone"]), str(r["building_id"]),
                             str(r["container"]), r["item"]))
    return rows, nodes


def aggregate_storage_by_location(
    buildings: list[dict[str, Any]],
    locations: dict[str, Any] | None = None,
    game_index: dict[str, list[Any]] | None = None,
    zone_places: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Складские запасы по поселениям; без поселения ключ - зона с пометкой."""
    rows, nodes = storage_rows(buildings, locations, game_index, zone_places)

    def key(settlement: str | None, zone: str | None) -> str:
        return settlement or ZONE_KEY_FORMAT.format(zone=zone)

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    buildings_per_loc: Counter[str] = Counter()
    for n in nodes:
        buildings_per_loc[key(n["settlement"], n["zone"])] += 1
    for r in rows:
        counts[key(r["settlement"], r["zone"])][r["item"]] += r["quantity"]
    result: dict[str, dict[str, Any]] = {}
    for loc in sorted(buildings_per_loc):
        cnt = counts[loc]
        result[loc] = {
            "buildings_count": buildings_per_loc[loc],
            "total_items_count": sum(cnt.values()),
            "items_summary": [{"name": name, "quantity": qty} for name, qty in cnt.most_common()],
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

    # 6. Носитель пленного уникального NPC
    for u in unique_npcs or []:
        uh = _clean_handle_tuple(u.get("handle"))

        if uh and uh in carried_targets:
            u["captured_by"] = carried_targets[uh]

    # 7. Агрегация складского баланса по локациям
    if isinstance(property_data, dict) and buildings:
        property_data["storage_by_location"] = aggregate_storage_by_location(buildings)
