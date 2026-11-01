"""Пространственное разрешение персонажей и построек Kenshi (kenshi-locations/1).

Модуль выполняет:
1. Строгий разбор 3D-координат персонажей и построек в диапазоне [-147456, 147456).
2. Разрешение персонажей строго по устойчивому characters[].id.
3. Разрешение построек property.buildings по составному ключу (zone, id).
4. Определение одного из 4 статусов resolution_status:
   - in_location: внутри confirmed_radius застройки (radius_source == 'game_buildings')
   - near_location: location=None, в окрестности известных локаций
   - in_region: location=None, в подтверждённом регионе растра areasmap.tga
   - unknown: location=None, region=None с явной причиной.
5. Детерминированные пространственные группы близости (spatial_groups).
6. Расчёт прямолинейного 2D-смещения (movement) строго при diff.valid == True.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any

MIN_COORD: float = -147456.0
MAX_COORD: float = 147456.0
ZONE_SIZE: float = 4608.0
RASTER_PIXEL_SIZE: float = 1152.0
RASTER_GRID_SIZE: int = 256

# Окрестность за пределами подтверждённой застройки для локаций с radius_source = "game_buildings"
# (2 пикселя растра по 1152 ед. = 2304 ед.)
GAME_BUILDINGS_VICINITY_EXTENSION: float = 2304.0


def parse_3d_coordinates(pos: Any) -> tuple[float, float, float] | None:
    """Строгий разбор 3D-координат: [x, y, z] или {'x': ..., 'z': ..., 'y': ...}.

    Принимает только числовые координаты в полуинтервале [-147456, 147456).
    Любые аномалии (bool, str, None, len != 3, NaN, Inf, выход за границы) -> None.
    """
    if pos is None:
        return None

    if isinstance(pos, (list, tuple)):
        if len(pos) != 3:
            return None
        x, y, z = pos[0], pos[1], pos[2]
    elif isinstance(pos, dict):
        if "x" not in pos or "z" not in pos:
            return None
        x = pos["x"]
        z = pos["z"]
        y = pos.get("y", 0.0)
    else:
        return None

    for val in (x, y, z):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        if math.isnan(val) or math.isinf(val):
            return None

    fx, fy, fz = float(x), float(y), float(z)
    if not (MIN_COORD <= fx < MAX_COORD and MIN_COORD <= fz < MAX_COORD):
        return None

    return (fx, fy, fz)


def compute_zone(x: float, z: float) -> str:
    """Вычисление индекса клетки сетки 64x64 (zone col.row)."""
    col = math.floor((x - MIN_COORD) / ZONE_SIZE)
    row = math.floor((z - MIN_COORD) / ZONE_SIZE)
    return f"{col}.{row}"


class RegionLookup:
    """Быстрый поиск региона и пограничного признака по RLE-растру карты регионов."""

    def __init__(self, world_ref: dict[str, Any]):
        self.palette = {r["index"]: r for r in world_ref.get("regions", [])}
        self.expanded: list[tuple[int, bool]] = []
        rle = world_ref.get("regions_map", {}).get("rle", [])
        for seg in rle:
            self.expanded.extend([(seg["index"], bool(seg.get("border", False)))] * seg["length"])

    def lookup(self, x: float, z: float) -> dict[str, Any] | None:
        px = min(255, max(0, math.floor((x - MIN_COORD) / RASTER_PIXEL_SIZE)))
        py = 255 - min(255, max(0, math.floor((z - MIN_COORD) / RASTER_PIXEL_SIZE)))
        idx = py * RASTER_GRID_SIZE + px
        if 0 <= idx < len(self.expanded):
            color_idx, is_border = self.expanded[idx]
            reg_meta = self.palette.get(color_idx)
            if reg_meta and reg_meta.get("name_en") != "NONE" and color_idx not in (0, 0x329999):
                return {
                    "name_en": reg_meta["name_en"],
                    "name_ru": reg_meta.get("name_ru"),
                    "border": is_border,
                    "index": reg_meta["index"],
                }
        return None


def resolve_locations(
    current: dict[str, Any],
    world_ref: dict[str, Any],
    snapshot_id: str | None = None,
    previous_locations: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Пространственное разрешение персонажей и построек снимка."""
    eff_snapshot_id = snapshot_id or current.get("snapshot_id") or "snapshot"
    world_ref_id = world_ref.get("world_ref_id")
    if not world_ref_id:
        raise ValueError("world_ref обязан содержать world_ref_id")

    region_lookup = RegionLookup(world_ref)
    locations_list = world_ref.get("locations", [])
    locs_by_id = {loc["instance_id"]: loc for loc in locations_list}

    # Индекс статических зданий: (zone, building_id) -> instance_id
    static_bldg_index: dict[tuple[str, int], str] = {}
    for loc in locations_list:
        for sb in loc.get("static_buildings", []):
            static_bldg_index[(sb["zone"], sb["building_id"])] = loc["instance_id"]

    # 1. Разрешение персонажей и животных строго по characters[].id
    resolved_characters: list[dict[str, Any]] = []
    seen_char_ids: set[str] = set()

    for char in current.get("characters", []):
        cid = char.get("id")
        if not cid or not str(cid).strip():
            raise ValueError("Критическая ошибка идентичности: у персонажа отсутствует или пуст 'id'")
        cid_str = str(cid).strip()
        if cid_str in seen_char_ids:
            raise ValueError(f"Критическая ошибка идентичности: дубликат id персонажа: '{cid_str}'")
        seen_char_ids.add(cid_str)

        cname = char.get("name") or "Без имени"
        squad_id = str(char.get("squad_id") or "default")
        raw_pos = char.get("position")
        parsed_pos = parse_3d_coordinates(raw_pos)

        c_warnings: list[str] = []

        if parsed_pos is None:
            c_warnings.append("Координаты персонажа отсутствуют, усечены или выходят за границы [-147456, 147456)")
            resolved_characters.append({
                "id": cid_str,
                "name": cname,
                "squad_id": squad_id,
                "coordinates": None,
                "zone": None,
                "resolution_status": "unknown",
                "location": None,
                "nearby_locations": [],
                "region": None,
                "warnings": c_warnings,
            })
            continue

        fx, fy, fz = parsed_pos
        czone = compute_zone(fx, fz)
        reg_dict = region_lookup.lookup(fx, fz)

        in_loc_cand: tuple[dict[str, Any], float] | None = None
        nearby: list[dict[str, Any]] = []

        for loc in locations_list:
            d = math.hypot(fx - loc["x"], fz - loc["z"])
            c_rad = loc["confirmed_radius"]
            r_src = loc.get("radius_source")

            # in_location строго для radius_source == "game_buildings"
            if r_src == "game_buildings" and d <= c_rad:
                if in_loc_cand is None or d < in_loc_cand[1]:
                    in_loc_cand = (loc, d)

            # Окрестность
            if r_src == "template_parameter":
                r_vic = c_rad
            else:
                r_vic = c_rad + GAME_BUILDINGS_VICINITY_EXTENSION

            if d <= r_vic:
                nearby.append({
                    "instance_id": loc["instance_id"],
                    "template_sid": loc["template_sid"],
                    "name_en": loc["name_en"],
                    "name_ru": loc.get("name_ru"),
                    "kind": loc["kind"],
                    "distance": round(d, 1),
                    "coordinate_source": "game_leveldata",
                })

        nearby.sort(key=lambda x: (x["distance"], x["instance_id"]))

        if in_loc_cand is not None:
            matched_loc, dist = in_loc_cand
            res_status = "in_location"
            res_loc = {
                "instance_id": matched_loc["instance_id"],
                "template_sid": matched_loc["template_sid"],
                "name_en": matched_loc["name_en"],
                "name_ru": matched_loc.get("name_ru"),
                "kind": matched_loc["kind"],
                "distance": round(dist, 1),
            }
        elif nearby:
            res_status = "near_location"
            res_loc = None
        elif reg_dict is not None:
            res_status = "in_region"
            res_loc = None
        else:
            res_status = "unknown"
            res_loc = None
            c_warnings.append("Точка находится вне регионов карты ('NONE') и вдали от известных локаций")

        resolved_characters.append({
            "id": cid_str,
            "name": cname,
            "squad_id": squad_id,
            "coordinates": [round(fx, 1), round(fy, 1), round(fz, 1)],
            "zone": czone,
            "resolution_status": res_status,
            "location": res_loc,
            "nearby_locations": nearby,
            "region": reg_dict,
            "warnings": c_warnings,
        })

    # Сортировка персонажей по ID
    resolved_characters.sort(key=lambda c: c["id"])

    # 2. Разрешение построек property.buildings по составному ключу (zone, id)
    resolved_buildings: list[dict[str, Any]] = []
    seen_building_keys: set[str] = set()
    raw_buildings = (current.get("property") or {}).get("buildings", [])

    for bldg in raw_buildings:
        bid = bldg.get("id")
        if bid is None or not str(bid).strip():
            raise ValueError("Критическая ошибка идентичности: у постройки отсутствует или пуст 'id'")
        bid_str = str(bid).strip()

        raw_zone = bldg.get("zone")
        b_warnings: list[str] = []

        if raw_zone is None or not str(raw_zone).strip():
            bldg_key = f"unknown:{bid_str}"
            zone_str: str | None = None
            b_warnings.append("У постройки отсутствует поле 'zone'")
        else:
            zone_str = str(raw_zone).strip()
            bldg_key = f"{zone_str}:{bid_str}"

        if bldg_key in seen_building_keys:
            raise ValueError(f"Критическая ошибка идентичности: дубликат составного ключа постройки: '{bldg_key}'")
        seen_building_keys.add(bldg_key)

        save_town_name = bldg.get("town")
        if save_town_name is not None:
            save_town_name = str(save_town_name).strip() or None

        parsed_bpos = parse_3d_coordinates(bldg.get("position"))
        if parsed_bpos is None:
            b_warnings.append("Координаты постройки отсутствуют, усечены или невалидны")
            resolved_buildings.append({
                "building_key": bldg_key,
                "id": bid_str,
                "zone": zone_str,
                "save_town_name": save_town_name,
                "coordinates": None,
                "resolution_status": "unknown",
                "location": None,
                "nearby_locations": [],
                "region": None,
                "warnings": b_warnings,
            })
            continue

        bfx, bfy, bfz = parsed_bpos
        computed_bzone = compute_zone(bfx, bfz)

        if zone_str is None:
            zone_str = computed_bzone
        elif zone_str != computed_bzone:
            b_warnings.append(f"Несовпадение заявленной зоны '{zone_str}' и вычисленной '{computed_bzone}'")

        breg_dict = region_lookup.lookup(bfx, bfz)

        # Вычисление окрестности
        bnearby: list[dict[str, Any]] = []
        for loc in locations_list:
            d = math.hypot(bfx - loc["x"], bfz - loc["z"])
            c_rad = loc["confirmed_radius"]
            r_src = loc.get("radius_source")
            r_vic = c_rad if r_src == "template_parameter" else c_rad + GAME_BUILDINGS_VICINITY_EXTENSION
            if d <= r_vic:
                bnearby.append({
                    "instance_id": loc["instance_id"],
                    "template_sid": loc["template_sid"],
                    "name_en": loc["name_en"],
                    "name_ru": loc.get("name_ru"),
                    "kind": loc["kind"],
                    "distance": round(d, 1),
                    "coordinate_source": "game_leveldata",
                })
        bnearby.sort(key=lambda x: (x["distance"], x["instance_id"]))

        # Сведение к instance_id:
        target_bldg_loc: dict[str, Any] | None = None
        target_dist: float = 0.0

        # (а) Статический индекс (только если zone присутствовал во входе)
        if raw_zone is not None and str(raw_zone).strip():
            int_bid = None
            if isinstance(bid, int):
                int_bid = bid
            elif isinstance(bid, str) and bid.isdigit():
                int_bid = int(bid)

            if int_bid is not None and (zone_str, int_bid) in static_bldg_index:
                target_iid = static_bldg_index[(zone_str, int_bid)]
                target_bldg_loc = locs_by_id.get(target_iid)
                if target_bldg_loc:
                    target_dist = math.hypot(bfx - target_bldg_loc["x"], bfz - target_bldg_loc["z"])

        # (б) Сверка непустого building.town при совпадении имени и попадании в confirmed_radius
        if target_bldg_loc is None and save_town_name:
            matched_by_name = [
                loc for loc in locations_list
                if loc.get("name_ru") == save_town_name or loc.get("name_en") == save_town_name
            ]
            if len(matched_by_name) == 1:
                cand = matched_by_name[0]
                d_cand = math.hypot(bfx - cand["x"], bfz - cand["z"])
                if d_cand <= cand["confirmed_radius"]:
                    target_bldg_loc = cand
                    target_dist = d_cand

        # Определение статуса постройки
        if target_bldg_loc is not None:
            b_status = "in_location"
            b_loc: dict[str, Any] | None = {
                "instance_id": target_bldg_loc["instance_id"],
                "template_sid": target_bldg_loc["template_sid"],
                "name_en": target_bldg_loc["name_en"],
                "name_ru": target_bldg_loc.get("name_ru"),
                "kind": target_bldg_loc["kind"],
                "distance": round(target_dist, 1),
            }
        else:
            # Чисто пространственная проверка
            spatial_match = None
            for loc in locations_list:
                d = math.hypot(bfx - loc["x"], bfz - loc["z"])
                if d <= loc["confirmed_radius"]:
                    spatial_match = (loc, d)
                    break

            if spatial_match is not None:
                sp_loc, sp_d = spatial_match
                if sp_loc.get("radius_source") == "game_buildings":
                    # Пустой или непротиворечивый town дает in_location
                    if not save_town_name or save_town_name in (sp_loc.get("name_ru"), sp_loc.get("name_en")):
                        b_status = "in_location"
                        b_loc = {
                            "instance_id": sp_loc["instance_id"],
                            "template_sid": sp_loc["template_sid"],
                            "name_en": sp_loc["name_en"],
                            "name_ru": sp_loc.get("name_ru"),
                            "kind": sp_loc["kind"],
                            "distance": round(sp_d, 1),
                        }
                    else:
                        b_status = "near_location"
                        b_loc = None
                        b_warnings.append(
                            f"Имя города постройки '{save_town_name}' не совпадает с локацией '{sp_loc['name_en']}'"
                        )
                else:
                    # template_parameter без подтверждения именем по правилу (б) -> near_location
                    b_status = "near_location"
                    b_loc = None
            elif bnearby:
                b_status = "near_location"
                b_loc = None
            elif breg_dict is not None:
                b_status = "in_region"
                b_loc = None
            else:
                b_status = "unknown"
                b_loc = None
                b_warnings.append("Постройка находится вне регионов карты ('NONE') и вдали от известных локаций")

        resolved_buildings.append({
            "building_key": bldg_key,
            "id": bid_str,
            "zone": zone_str,
            "save_town_name": save_town_name,
            "coordinates": [round(bfx, 1), round(bfy, 1), round(bfz, 1)],
            "resolution_status": b_status,
            "location": b_loc,
            "nearby_locations": bnearby,
            "region": breg_dict,
            "warnings": b_warnings,
        })

    # Сортировка построек по составному ключу
    resolved_buildings.sort(key=lambda b: b["building_key"])

    # 3. Детерминированные пространственные группы близости (spatial_groups)
    groups_map: dict[tuple, dict[str, Any]] = {}

    for c in resolved_characters:
        status = c["resolution_status"]
        loc = c.get("location")
        reg = c.get("region")

        if status == "in_location" and loc:
            gkey = ("in_location", loc["instance_id"])
            lbl = loc.get("name_ru") or loc.get("name_en") or loc["instance_id"]
            g_loc = loc
            g_reg = reg
        elif status == "near_location" and c.get("nearby_locations"):
            top_near = c["nearby_locations"][0]
            gkey = ("near_location", top_near["instance_id"])
            near_name = top_near.get("name_ru") or top_near.get("name_en")
            lbl = f"Окрестности {near_name}"
            g_loc = None
            g_reg = reg
        elif status == "in_region" and reg:
            gkey = ("in_region", reg["name_en"])
            reg_name = reg.get("name_ru") or reg.get("name_en")
            lbl = f"Регион {reg_name}"
            g_loc = None
            g_reg = reg
        else:
            gkey = ("unknown", "")
            lbl = "Неизвестное местоположение"
            g_loc = None
            g_reg = None

        if gkey not in groups_map:
            groups_map[gkey] = {
                "label": lbl,
                "resolution_status": status,
                "location": g_loc,
                "region": g_reg,
                "members": [],
            }
        groups_map[gkey]["members"].append({
            "id": c["id"],
            "name": c["name"],
            "squad_id": c["squad_id"],
        })

    # Детерминированное упорядочение групп и участников внутри групп
    raw_groups = list(groups_map.values())
    for g in raw_groups:
        g["members"].sort(key=lambda m: (str(m["name"] or ""), str(m["id"] or "")))

    raw_groups.sort(key=lambda g: (-len(g["members"]), str(g["label"])))

    spatial_groups: list[dict[str, Any]] = []
    for idx, g in enumerate(raw_groups, start=1):
        spatial_groups.append({
            "group_id": f"group-{idx}",
            "label": g["label"],
            "resolution_status": g["resolution_status"],
            "location": g["location"],
            "region": g["region"],
            "characters": g["members"],
        })

    # 4. Перемещение (movement) строго при diff.valid == true
    diff_is_valid = bool(changes and changes.get("valid") is True and previous_locations is not None)
    if not diff_is_valid:
        from_snap = None
        if changes and changes.get("from"):
            f_ref = changes["from"]
            from_snap = f_ref.get("snapshot_id") if isinstance(f_ref, dict) else f_ref
        elif previous_locations:
            from_snap = previous_locations.get("snapshot_id")

        reason = None
        if changes and changes.get("reason"):
            reason = changes.get("reason")
        elif changes and changes.get("problems"):
            reason = "; ".join(changes.get("problems", []))
        if not reason:
            reason = "базовое сравнение отсутствует (diff.valid == false либо нет previous_locations)"

        movement = {
            "valid": False,
            "from_snapshot_id": from_snap,
            "to_snapshot_id": eff_snapshot_id,
            "reason": reason,
            "characters": [],
        }
    else:
        prev_chars_map = {c["id"]: c for c in previous_locations.get("characters", [])}
        movements_list: list[dict[str, Any]] = []

        for c_curr in resolved_characters:
            cid = c_curr["id"]
            if cid in prev_chars_map:
                c_prev = prev_chars_map[cid]
                from_loc = c_prev["location"]["name_en"] if c_prev.get("location") else None
                to_loc = c_curr["location"]["name_en"] if c_curr.get("location") else None
                from_reg = c_prev["region"]["name_en"] if c_prev.get("region") else None
                to_reg = c_curr["region"]["name_en"] if c_curr.get("region") else None

                coords_curr = c_curr.get("coordinates")
                coords_prev = c_prev.get("coordinates")
                if coords_curr and coords_prev:
                    d_2d = round(math.hypot(coords_curr[0] - coords_prev[0], coords_curr[2] - coords_prev[2]), 1)
                else:
                    d_2d = None

                movements_list.append({
                    "character_id": cid,
                    "name": c_curr["name"],
                    "from_resolution_status": c_prev["resolution_status"],
                    "to_resolution_status": c_curr["resolution_status"],
                    "from_location": from_loc,
                    "to_location": to_loc,
                    "from_region": from_reg,
                    "to_region": to_reg,
                    "distance_2d": d_2d,
                    "is_straight_line": True,
                })

        movements_list.sort(key=lambda m: (str(m["name"] or ""), str(m["character_id"] or "")))

        movement = {
            "valid": True,
            "from_snapshot_id": previous_locations.get("snapshot_id"),
            "to_snapshot_id": eff_snapshot_id,
            "reason": None,
            "characters": movements_list,
        }

    # Сводные счетчики
    meta = {
        "total_characters": len(resolved_characters),
        "resolved_characters": sum(1 for c in resolved_characters if c["resolution_status"] != "unknown"),
        "total_buildings": len(resolved_buildings),
        "resolved_buildings": sum(1 for b in resolved_buildings if b["resolution_status"] != "unknown"),
        "characters_by_status": dict(Counter(c["resolution_status"] for c in resolved_characters)),
        "buildings_by_status": dict(Counter(b["resolution_status"] for b in resolved_buildings)),
    }

    created_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "format": "kenshi-locations/1",
        "snapshot_id": eff_snapshot_id,
        "world_ref_id": world_ref_id,
        "created_at": created_at,
        "meta": meta,
        "characters": resolved_characters,
        "buildings": resolved_buildings,
        "spatial_groups": spatial_groups,
        "movement": movement,
    }
