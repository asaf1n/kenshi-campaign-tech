"""Детерминированный реестр NPC, боссов и Мейто из переданных данных выпуска."""
from __future__ import annotations

from collections import defaultdict
import copy
import re
from typing import Any

from tools.chain_timeline import game_clock
from tools.io_utils import canonical
from tools.location_registry import _game_minutes, merge_progress
from tools.tables import SQUAD_ROLES, WEAPON_SECTIONS, _names, _refs, _stats
from tools.validation import validate_event, validate_unique_entities_registry
from tools.world_context import ZONE_KEY_FORMAT, is_valid_strict_handle, storage_rows, zone_settlements

GRADE_SID = "52293-rebirth.mod"
MANUFACTURER_SID = "52288-rebirth.mod"
TROPHY_MARKER = re.compile(r"\[base_sid=([a-zA-Z0-9_\-\.]+)\]")
TOWN_LINKS = {"residents": "resident", "bar squads": "bar_squad", "roaming squads": "roaming"}
# Подтверждённые заданием боссы. Остальные выводятся из игровых признаков.
CUSTOM_BOSS_SIDS = frozenset({"50519-rebirth.mod", "96407-rebirth.mod", "96409-rebirth.mod"})


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Убирает повтор одного факта, сохраняя разные пути и экземпляры."""
    return [row for _, row in sorted({canonical(row): row for row in rows}.items())]


def _by_sid(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        sid = row["sid"]
        if sid in result:
            raise ValueError(f"Повтор SID в реестре: {sid}")
        result[sid] = row
    return result


def _handle(value: Any) -> tuple[int, ...] | None:
    return is_valid_strict_handle(value) if isinstance(value, (list, tuple)) else None


def _location_links(units: dict[str, Any], world_ref: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    parents: dict[str, set[str]] = defaultdict(set)
    for squad in units.get("squads") or []:
        for role in SQUAD_ROLES:
            for child, _ in _refs(squad, role):
                parents[child].add(squad["sid"])
    towns: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for town in units.get("towns") or []:
        for category, kind in TOWN_LINKS.items():
            for squad_sid, _ in _refs(town, category):
                towns[squad_sid].append((town, kind))
    instances: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for location in world_ref.get("locations") or []:
        instances[location["template_sid"]][location["instance_id"]] = location

    result = {}
    for npc in units.get("characters") or []:
        sid = npc["sid"]
        links = []
        pending = [(sid, [f"CHARACTER:{sid}"], {sid})]
        while pending:
            child, chain, seen = pending.pop()
            for squad_sid in sorted(parents.get(child, ())):
                if squad_sid in seen:
                    continue
                squad_chain = [*chain, f"SQUAD:{squad_sid}"]
                pending.append((squad_sid, squad_chain, seen | {squad_sid}))
                for town, kind in towns.get(squad_sid, []):
                    template_sid = town["sid"]
                    locations = instances.get(template_sid, {})
                    resolution = "unresolved" if not locations else "unique" if len(locations) == 1 else "ambiguous"
                    for location in locations.values() or [{}]:
                        instance_id = location.get("instance_id", "")
                        links.append({
                            "instance_id": instance_id, "template_sid": template_sid,
                            "location_name_en": location.get("name_en") or town.get("name") or template_sid,
                            "location_name_ru": location.get("name_ru"), "region": location.get("region"),
                            "link_type": kind, "resolution": resolution,
                            "source_chain": [*squad_chain, f"TOWN:{template_sid}"]
                            + ([f"INSTANCE:{instance_id}"] if instance_id else []),
                        })
        result[sid] = sorted(_ordered(links), key=lambda row: (
            row["instance_id"], row["template_sid"], row["link_type"], row["source_chain"]))
    return result


def _dynamic(npc: dict[str, Any], characters: dict[tuple[int, ...], dict[str, Any]],
             release: dict[str, Any]) -> dict[str, Any]:
    handle = _handle(npc.get("handle"))
    character = characters.get(handle) if handle else None
    status = "unresolved"
    if handle:
        status = "confirmed_in_squad" if character else "confirmed_not_in_squad"
    time = release["game_time"]
    return {
        "state_code": npc.get("state_code"), "player_defeat_flag": npc.get("player"),
        "squad_status": status, "character_id": character.get("id") if character else None,
        "handle": list(handle) if handle else None, "captured_by": npc.get("captured_by"),
        "snapshot_id": release["snapshot_id"], "game_time": str(time) if time is not None else None,
    }


def _observed(cur: dict[str, Any], world_ref: dict[str, Any],
              game_index: dict[str, list[Any]]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    squad_names = {row["squad_id"]: row.get("squad_name") for row in cur.get("squads") or []}

    def add(item: dict[str, Any], where: dict[str, Any]) -> None:
        if (item.get("material_sid"), item.get("company_sid")) != (GRADE_SID, MANUFACTURER_SID):
            return
        # Сводные строки старых снимков не содержат идентичности экземпляра.
        if not item.get("item_sid") or not item.get("base_data_sid"):
            return
        found[item["base_data_sid"]].append({
            "item_sid": item["item_sid"], "carrier_character_id": None, "carrier_name": None,
            "carrier_squad": None, "storage_location_key": None, "storage_settlement": None,
            "storage_building": None, "slot": item.get("section"), **where,
        })

    for character in cur.get("characters") or []:
        for item in character.get("items") or []:
            container = item.get("container") or ""
            kind = "inventory"
            if container.startswith("inventory>"):
                kind = "backpack"
            elif container == "inventory" and item.get("section") in WEAPON_SECTIONS:
                kind = "equipped"
            add(item, {"carrier_character_id": character.get("id"), "carrier_name": character.get("name"),
                       "carrier_squad": character.get("squad_name") or squad_names.get(character.get("squad_id")),
                       "location_kind": kind})

    buildings = (cur.get("property") or {}).get("buildings") or []
    _, nodes = storage_rows(buildings, game_index=game_index, zone_places=zone_settlements(None, world_ref))
    places = {(row["zone"], row["building_id"]): row for row in nodes}
    pending = [(building, None) for building in buildings]
    while pending:
        building, parent_zone = pending.pop()
        zone = building.get("zone") or parent_zone
        place = places[(zone, building.get("id"))]
        for item in building.get("storage_items") or []:
            add(item, {"storage_location_key": place["settlement"] or ZONE_KEY_FORMAT.format(zone=zone),
                       "storage_settlement": place["settlement"], "storage_building": place["building"],
                       "location_kind": "storage"})
        pending.extend((child, zone) for child in building.get("interior") or [])
    return {sid: sorted(_ordered(rows), key=lambda row: (row["item_sid"], canonical(row)))
            for sid, rows in found.items()}


def _trophies(timeline_data: dict[str, Any] | None, game_time: Any) -> dict[str, dict[str, Any]]:
    events = (timeline_data or {}).get("events") or []
    current_minutes = _game_minutes(game_time)
    applicable = []
    for event in events:
        event_minutes = _game_minutes(event.get("game_time"))
        day = event.get("game_day")
        if current_minutes is not None:
            if event_minutes is not None and event_minutes > current_minutes:
                continue
            if day is not None and day > current_minutes // 1440:
                continue
        applicable.append(event)
    superseded = {event["corrects_event_id"] for event in applicable
                  if event.get("kind") == "correction" and event.get("status") != "inferred"}
    result = {}
    for event in sorted(applicable, key=lambda row: row["event_id"]):
        if event.get("status") != "confirmed" or event["event_id"] in superseded:
            continue
        validate_event(event)
        day = event.get("game_day")
        for sid in TROPHY_MARKER.findall(event["summary"]):
            result.setdefault(sid, {"event_id": event["event_id"], "day": day, "summary": event["summary"]})
    return result


def build_unique_entities_registry(
    cur: dict[str, Any], world_ref: dict[str, Any], game_records: dict[str, Any],
    game_index: dict[str, list[Any]], progress_data: dict[str, Any] | None = None,
    timeline_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает реестр без чтения файлов и изменения входов.

    Вызывающая сторона дополняет current полями snapshot_id, campaign_id и generated_at
    из метаданных выпуска. Для generated_at допустим created_at справочника мира.
    timeline_data содержит уже отобранную активную цепочку и проверенные файловые источники.
    """
    game_time = cur.get("game_time")
    if game_time is None:
        _, game_time = game_clock(cur.get("world"))
    result: dict[str, Any] = {
        "format": "kenshi-unique-entities-registry/1", "campaign_id": cur.get("campaign_id"),
        "snapshot_id": cur["snapshot_id"], "world_ref_id": world_ref["world_ref_id"],
        "game_time": game_time, "generated_at": cur.get("generated_at") or world_ref["created_at"],
    }
    units = game_records.get("units") or {}
    templates = _by_sid(units.get("characters") or [])
    dynamic = _by_sid((cur.get("world_state") or {}).get("unique_npcs") or [])
    locations = _location_links(units, world_ref)
    characters = {}
    for character in cur.get("characters") or []:
        handle = _handle(character.get("handle"))
        if handle:
            if handle in characters:
                raise ValueError("Повтор полного handle персонажа отряда")
            characters[handle] = character

    states: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in (game_records.get("reactions") or {}).get("world_states") or []:
        for condition in ("NPC is", "NPC is NOT"):
            for sid, _ in _refs(state, condition):
                # Условия шаблона не являются текущим значением WORLD_EVENT_STATE.
                states[sid].append({"state_sid": state["sid"], "state_name": state.get("name") or state["sid"],
                                    "value": None})
    holders: dict[str, set[str]] = defaultdict(set)
    origins = {}
    for sid, template in sorted(templates.items()):
        meitou = MANUFACTURER_SID in {target for target, _ in _refs(template, "weapon level")}
        if meitou:
            for weapon_sid, _ in _refs(template, "weapons"):
                holders[weapon_sid].add(sid)
        bounty = (template.get("int") or {}).get("bounty amount", 0)
        if bounty > 0:
            origins[sid] = "game_bounty"
        elif sid in states:
            origins[sid] = "world_state"
        elif meitou:
            origins[sid] = "meitou_holder"
        elif sid in CUSTOM_BOSS_SIDS:
            origins[sid] = "custom_catalog"
    for sid in states:
        origins.setdefault(sid, "world_state")

    marked = merge_progress({**result, "locations": [{"instance_id": row["instance_id"]}
                                                    for row in world_ref.get("locations") or []]}, progress_data)
    marks = {row["instance_id"]: row["cleared"] for row in marked["locations"] if "cleared" in row}
    npc_sids = set(dynamic) | set(origins) | {sid for sid, row in templates.items()
                                           if (row.get("bool") or {}).get("unique")}
    npcs, bosses = [], []
    for sid in sorted(npc_sids):
        template = templates.get(sid, {})
        observed = dynamic.get(sid, {})
        name_en, name_ru = _names(game_index, sid)
        name_en = name_en or template.get("name") or observed.get("name") or sid
        name_ru = name_ru or template.get("name_ru") or observed.get("name_ru")
        links = locations.get(sid, [])
        status = _dynamic(observed, characters, result)
        npc_class = (template.get("int") or {}).get("NPC class")
        category = "unique_npc"
        if npc_class == 9 or any(link["link_type"] == "bar_squad" for link in links):
            category = "recruit"
        if sid in origins:
            category = "boss"
        if npc_class == 5:
            category = "world_leader"
        npcs.append({"npc_sid": sid, "name_en": name_en, "name_ru": name_ru, "category": category,
                     "locations": links, "dynamic": status})
        if sid in origins:
            amount = (template.get("int") or {}).get("bounty amount", 0)
            clearance = None
            for link in links:
                mark = marks.get(link["instance_id"])
                if mark is not None:
                    clearance = {"location_instance_id": link["instance_id"],
                                 "cleared_at": mark.get("game_time", ""), "source": mark["evidence"]}
                    break
            bosses.append({
                "npc_sid": sid, "name_en": name_en, "name_ru": name_ru, "classification_origin": origins[sid],
                "bounty": {"amount": amount, "factions": sorted({target for target, _ in _refs(template, "bounty factions")})}
                if amount > 0 else None,
                "associated_world_states": sorted(_ordered(states.get(sid, [])), key=lambda row: row["state_sid"]),
                "static_locations": links,
                "observed_state": {key: status[key] for key in ("state_code", "player_defeat_flag", "captured_by")},
                "cleared_evidence": clearance,
            })

    observed_weapons = _observed(cur, world_ref, game_index)
    trophies = _trophies(timeline_data, game_time)
    weapons = []
    for sid in sorted(set(holders) | set(observed_weapons)):
        name_en, name_ru = _names(game_index, sid)
        static_holders = []
        for npc_sid in sorted(holders.get(sid, ())):
            for link in locations.get(npc_sid) or [{}]:
                static_holders.append({"npc_sid": npc_sid, "npc_name": templates[npc_sid].get("name") or npc_sid,
                                       "town_instance_id": link.get("instance_id") or None,
                                       "town_name": link.get("location_name_en")})
        category = _stats(game_index, sid).get("skill category")
        weapons.append({
            "base_sid": sid, "grade_sid": GRADE_SID, "manufacturer_sid": MANUFACTURER_SID,
            "name_en": name_en or sid, "name_ru": name_ru,
            "weapon_category": str(category) if category is not None else None,
            "static_holders": sorted(_ordered(static_holders), key=lambda row: (
                row["npc_sid"], row["town_instance_id"] or "", row["town_name"] or "")),
            "observed_instances": observed_weapons.get(sid, []),
            "provenance_status": "confirmed_trophy" if sid in trophies else "observed_in_inventory",
            "trophy_evidence": trophies.get(sid),
        })
    result.update(unique_npcs=npcs, bosses=bosses, meitou_weapons=weapons)
    validate_unique_entities_registry(result)
    return result


def apply_progress_to_unique_entities(
    entities: dict[str, Any],
    progress_data: dict[str, Any] | None,
    world_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Переносит отметки зачистки кампании в боссов реестра; чистая функция без побочных эффектов."""
    result = copy.deepcopy(entities)
    bosses = result.get("bosses", [])
    if progress_data is None:
        for boss in bosses:
            boss["cleared_evidence"] = None
        return result

    if world_ref and world_ref.get("locations"):
        all_locations = [{"instance_id": r["instance_id"]} for r in world_ref.get("locations", [])]
    else:
        unique_iids: set[str] = set()
        for b in bosses:
            for link in b.get("static_locations") or b.get("locations") or []:
                iid = link.get("instance_id")
                if iid:
                    unique_iids.add(iid)
        all_locations = [{"instance_id": iid} for iid in sorted(unique_iids)]

    synth_reg = {
        "campaign_id": entities.get("campaign_id", ""),
        "game_time": entities.get("game_time", ""),
        "locations": all_locations,
    }
    marked = merge_progress(synth_reg, progress_data)
    marks = {row["instance_id"]: row["cleared"] for row in marked.get("locations", []) if "cleared" in row}

    for boss in bosses:
        clearance = None
        for link in boss.get("static_locations") or boss.get("locations") or []:
            iid = link.get("instance_id")
            if iid and iid in marks:
                clearance = {
                    "location_instance_id": iid,
                    "cleared_at": marks[iid].get("game_time", ""),
                    "source": marks[iid].get("evidence", ""),
                }
                break
        boss["cleared_evidence"] = clearance

    return result
