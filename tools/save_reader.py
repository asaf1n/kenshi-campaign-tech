"""Нативное чтение сохранений Kenshi: quick.save, platoon/*.platoon и zone/*.zone.

Модуль читает данные напрямую через tools.kenshi_gd и tools.gamedb без внешнего конвертера.
Входные файлы сохранений рассматриваются как строго read-only.
"""
from __future__ import annotations

import datetime
import os
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from tools.kenshi_gd import read_file
from tools.task_types import get_task_name

PathLike = str | os.PathLike[str]
DEFAULT_PLAYER_FACTION = "204-gamedata.base"

LIMBS: tuple[str, ...] = ("left_arm", "right_arm", "left_leg", "right_leg")
BASE_LIMB_SIDS: dict[str, str] = {
    "28-gamedata.quack": "left_arm",
    "29-gamedata.quack": "right_arm",
    "30-gamedata.quack": "left_leg",
    "31-gamedata.quack": "right_leg",
}

ATTRIBUTES: tuple[str, ...] = ("strength", "dexterity", "toughness2", "perception")

STAT_UI: dict[str, str] = {
    "strength": "Strength", "dexterity": "Dexterity", "toughness2": "Toughness",
    "perception": "Perception", "attack": "Melee Attack", "defence": "Melee Defence",
    "dodge": "Dodge", "unarmed": "Martial Arts", "katana": "Katanas", "sabres": "Sabres",
    "hackers": "Hackers", "heavy weapons": "Heavy Weapons", "blunt": "Blunt",
    "poles": "Polearms", "bow": "Crossbows", "turrets": "Turrets", "ff": "Precision Shooting",
    "stealth": "Stealth", "lockpicking": "Lockpicking", "thievery": "Thievery",
    "assassin": "Assassination", "athletics": "Athletics", "swimming": "Swimming",
    "science": "Science", "engineer": "Engineering", "robotics": "Robotics",
    "weapon smith": "Weapon Smithing", "armour smith": "Armour Smithing",
    "bow smith": "Crossbow Smithing", "medic": "Field Medic", "cooking": "Cooking",
    "farming": "Farming", "labouring": "Labouring",
}

STATS_IN_FCS: set[str] = set(STAT_UI.keys())
SKILL_NAMES: dict[str, str] = {
    k: ("Precision Shooting" if k == "ff" else v)
    for k, v in STAT_UI.items() if k in STATS_IN_FCS
}

HEALTH_FIELDS: tuple[str, ...] = ("flesh", "hit", "stun", "bandage", "wear", "rig", "hitmult")
CONFIRMED_GRADES: dict[int, str] = {80: "Специальный", 95: "Мастерский"}


class SaveReadError(Exception):
    """Ошибка чтения или повреждения структуры сохранения."""


def _r(val: Any, nd: int = 2) -> Any:
    if isinstance(val, float):
        return round(val, nd)
    return val


def indexed(d: dict[str, Any], prefix: str) -> list[Any]:
    """Значения ключей '<prefix>0', '<prefix>1', ... в числовом порядке."""
    n = len(prefix)
    keys = [(int(k[n:]), k) for k in d if k.startswith(prefix) and k[n:].isdigit()]
    return [d[k] for _, k in sorted(keys)]


def town_handle(i: dict[str, int] | None, prefix: str = "hand") -> list[int | None] | None:
    """Извлекает 5-компонентный handle объекта/города (TYPE, C, S, I, CS)."""
    if not i:
        return None
    keys = ("TYPE", "C", "S", "I", "CS")
    vals = [i.get(prefix + k) for k in keys]
    if all(v is None for v in vals):
        return None
    return vals


def _dir_state(d: PathLike) -> tuple[tuple[str, int, int], ...]:
    p = Path(d)
    if not p.is_dir():
        return ()
    out: list[tuple[str, int, int]] = []
    for item in sorted(p.iterdir()):
        if item.is_file():
            st = item.stat()
            out.append((item.name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def save_signature(save_dir: PathLike) -> tuple[Any, ...]:
    """Отпечаток файлов сохранения для контроля неизменности во время чтения."""
    sdir = Path(save_dir)
    qs = sdir / "quick.save"
    if not qs.is_file():
        return ()
    st = qs.stat()
    return (
        st.st_size,
        st.st_mtime_ns,
        _dir_state(sdir / "platoon"),
        _dir_state(sdir / "zone"),
    )


def parse_research(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Запись 21 quick.save: завершённые, текущие и оплаченные исследования."""
    if not rec:
        return None
    s, f = rec.get("string", {}), rec.get("float", {})
    return {
        "finished": indexed(s, "finished"),
        "paid": indexed(s, "paid"),
        "current": indexed(s, "current"),
        "current_progress": indexed(f, "current prog"),
        "num_finished": f.get("num finished"),
        "num_currents": f.get("num currents"),
    }


def parse_towns(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Записи 94 'Town state <имя>': handle, имя, присутствующие фракции, замещение."""
    out: list[dict[str, Any]] = []
    for r in records:
        if r.get("type") != 94:
            continue
        name = r.get("name", "")
        clean_name = name[len("Town state "):] if name.startswith("Town state ") else name
        s, i = r.get("string", {}), r.get("int", {})
        out.append({
            "handle": town_handle(i, "hand"),
            "name": clean_name,
            "raw_name": name,
            "faction": s.get("faction"),
            "factions_here": indexed(s, "factions here"),
            "replacement_town": s.get("replacementTown") or None,
            "instance": s.get("instance"),
            "building_material": s.get("building material"),
        })
    return out


def parse_town_discovery(records: Sequence[dict[str, Any]]) -> dict[str, bool]:
    """Признак обнаружения Town state для однозначно определённого экземпляра."""
    seen: set[str] = set()
    result: dict[str, bool] = {}
    for record in records:
        if record.get("type") != 94:
            continue
        instance = record.get("string", {}).get("instance")
        if not instance:
            continue
        discovered = record.get("bool", {}).get("discovered")
        if instance in seen:
            result.pop(instance, None)
        elif isinstance(discovered, bool):
            result[instance] = discovered
        seen.add(instance)
    return result


def parse_registry(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Записи 34: реестр отрядов сохранения (stem -> метаданные)."""
    reg: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.get("type") != 34:
            continue
        s, b, i = r.get("string", {}), r.get("bool", {}), r.get("int", {})
        stem = s.get("platoon stringID") or r.get("sid", "")
        if stem:
            reg[stem] = {
                "name": r.get("name", ""),
                "faction_sid": s.get("faction stringID"),
                "faction_name": s.get("faction name"),
                "squad_index": i.get("squad index"),
                "dead": b.get("dead"),
            }
    return reg


def parse_diplomacy(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Записи 37: дипломатия фракций, численные отношения и доверие."""
    out: list[dict[str, Any]] = []
    for r in records:
        if r.get("type") != 37:
            continue
        s, f = r.get("string", {}), r.get("float", {})
        relations: dict[str, dict[str, float | None]] = {}
        for k, sid_val in s.items():
            if k.startswith("relationSID") and k[len("relationSID"):].isdigit():
                idx = k[len("relationSID"):]
                rel_val = f.get(f"relation{idx}")
                trust_val = f.get(f"trust{idx}")
                trust_neg_val = f.get(f"trustNeg{idx}")
                if rel_val is not None:
                    relations[sid_val] = {
                        "relation": rel_val,
                        "trust": trust_val,
                        "trust_neg": trust_neg_val,
                    }
        out.append({
            "sid": r.get("sid", ""),
            "name": r.get("name", ""),
            "relations": relations,
        })
    return out


def parse_uniques(state_rec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Запись 56: сырой трекинг состояния уникальных NPC мира без домысливания."""
    if not state_rec:
        return []
    s, i, b = state_rec.get("string", {}), state_rec.get("int", {}), state_rec.get("bool", {})
    uniques: list[dict[str, Any]] = []

    # Сопоставляем по числовому суффиксу ключа (устранение Н4)
    key_nums = sorted({
        int(k[len("usedUniques"):])
        for k in s
        if k.startswith("usedUniques") and k[len("usedUniques"):].isdigit()
    })

    for num in key_nums:
        sid_val = s.get(f"usedUniques{num}")
        st_code = i.get(f"usedUniquesState{num}")
        pl_val = b.get(f"usedUniquesPlayer{num}")
        handle = town_handle(i, f"usedUniques{num}handle")
        uniques.append({
            "index": num,
            "sid": sid_val,
            "state_code": st_code,
            "player": pl_val,
            "handle": handle,
        })
    return uniques


def extract_diplomacy(
    records: Sequence[dict[str, Any]],
    player_faction_sid: str = DEFAULT_PLAYER_FACTION,
    gamedb_inst: Any = None,
) -> dict[str, Any]:
    """Извлекает полную дипломатическую карту отношений игрока со всеми фракциями мира.

    Канонические sid берутся из 'gamedata stringID' (записи типа 37).
    Если связь между фракцией и игроком отсутствует в записи, relation устанавливается в None
    (отсутствие связи не подменяется нулём). Оценочные классы отношений не вычисляются.
    """
    fac_records = [r for r in records if r.get("type") == 37]
    gdb = gamedb_inst if isinstance(gamedb_inst, dict) else None

    # Отображение динамического record sid -> канонический gamedata stringID
    sid_to_gid: dict[str, str] = {}
    for r in fac_records:
        rec_sid = r.get("sid", "")
        gid = r.get("string", {}).get("gamedata stringID") or rec_sid
        sid_to_gid[rec_sid] = gid

    # Поиск записи игрока
    player_rec = next(
        (r for r in fac_records if r.get("string", {}).get("gamedata stringID") == player_faction_sid or r.get("sid") == player_faction_sid),
        None,
    )
    player_rec_sid = player_rec.get("sid", "") if player_rec else ""
    player_name = (player_rec.get("name") if player_rec else "") or "Безымянные"

    rel_map: dict[str, dict[str, Any]] = {}

    # Читаем отношения других фракций к игроку
    for r in fac_records:
        r_sid = r.get("sid", "")
        if r_sid == player_rec_sid:
            continue
        canon_sid = sid_to_gid.get(r_sid, r_sid)
        r_name = r.get("name", "")
        if not r_name and gdb and canon_sid in gdb:
            g_entry = gdb[canon_sid]
            r_name = g_entry[1] if len(g_entry) > 1 and g_entry[1] else g_entry[0]

        r_str, r_flt = r.get("string", {}), r.get("float", {})
        rel_to_player: float | None = None
        trust_to_player: float | None = None
        trust_neg_to_player: float | None = None

        for k, target_sid in r_str.items():
            if k.startswith("relationSID") and target_sid in (player_rec_sid, player_faction_sid):
                idx = k[len("relationSID"):]
                rel_val = r_flt.get(f"relation{idx}")
                if rel_val is not None:
                    rel_to_player = round(float(rel_val), 2)
                    trust_to_player = _r(r_flt.get(f"trust{idx}"))
                    trust_neg_to_player = _r(r_flt.get(f"trustNeg{idx}"))
                    break

        # Не подменяем отсутствие связи на 0.0: сохраняем None
        rel_map[canon_sid] = {
            "faction_sid": canon_sid,
            "faction_name": r_name or canon_sid,
            "relation": rel_to_player,
            "trust": trust_to_player,
            "trust_neg": trust_neg_to_player,
        }

    # Сортировка: сначала с известным relation (по убыванию), затем unknown, при равенстве по faction_sid
    sorted_relations = sorted(
        rel_map.values(),
        key=lambda x: (x["relation"] is None, -(x["relation"] or 0.0), x["faction_sid"]),
    )

    summary = {
        "total_factions": len(sorted_relations),
    }

    return {
        "player_faction_sid": player_faction_sid,
        "player_faction_name": player_name,
        "relations": sorted_relations,
        "summary": summary,
    }


def extract_world_state(
    records: Sequence[dict[str, Any]],
    gamedb_inst: Any = None,
) -> dict[str, Any]:
    """Извлекает состояние мира: уникальные NPC-лидеры и переопределения городов."""
    rec56 = next((r for r in records if r.get("type") == 56), None)
    raw_uniques = parse_uniques(rec56)
    gdb = gamedb_inst if isinstance(gamedb_inst, dict) else None

    uniques_list: list[dict[str, Any]] = []
    for u in raw_uniques:
        sid = u.get("sid", "")
        entry = gdb.get(sid) if (gdb and sid in gdb) else None
        name = entry[0] if entry else sid
        name_ru = entry[1] if entry and len(entry) > 1 and entry[1] else name
        st_code = u.get("state_code")
        uniques_list.append({
            "index": u.get("index"),
            "sid": sid,
            "name": name,
            "name_ru": name_ru,
            "state_code": st_code,
            "player": u.get("player"),
            "handle": u.get("handle"),
        })

    uniques_list.sort(key=lambda x: x["index"])

    towns_list: list[dict[str, Any]] = []
    all_towns_count = 0
    for r in records:
        if r.get("type") != 94:
            continue
        all_towns_count += 1
        name = r.get("name", "")
        clean_name = name[len("Town state "):] if name.startswith("Town state ") else name
        s, i = r.get("string", {}), r.get("int", {})
        rep = s.get("replacementTown")
        if not rep:
            continue

        rep_entry = gdb.get(rep) if (gdb and rep in gdb) else None
        rep_name = rep_entry[0] if rep_entry else rep
        rep_name_ru = rep_entry[1] if rep_entry and len(rep_entry) > 1 and rep_entry[1] and rep_entry[1] != rep_name else None
        _, marker, variant_tail = rep_name.partition("(override)")
        variant = variant_tail.strip() if marker and variant_tail.strip() else ("" if marker else None)

        towns_list.append({
            "name": clean_name,
            "raw_name": name,
            "variant": variant,
            "replacement_sid": rep,
            "replacement_name": rep_name,
            "replacement_name_ru": rep_name_ru,
            "faction": s.get("faction"),
            "factions_here": indexed(s, "factions here"),
            "handle": town_handle(i, "hand"),
        })

    towns_list.sort(key=lambda x: x["name"])

    # Сводка ведется строго по числовым кодам состояния без домысливания
    summary = {
        "total_uniques": len(uniques_list),
        "state_0_count": sum(1 for u in uniques_list if u["state_code"] == 0),
        "state_1_count": sum(1 for u in uniques_list if u["state_code"] == 1),
        "state_2_count": sum(1 for u in uniques_list if u["state_code"] == 2),
        "total_towns": all_towns_count,
        "overridden_towns_count": len(towns_list),
    }

    result = {
        "unique_npcs": uniques_list,
        "town_overrides": towns_list,
        "summary": summary,
    }
    discovery = parse_town_discovery(records)
    if discovery:
        result["town_discovery"] = [
            {"instance_id": instance, "discovered": value}
            for instance, value in sorted(discovery.items())
        ]
    return result


def parse_quick_save(
    data: bytes,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    gamedb_inst: Any = None,
) -> dict[str, Any]:
    """Полный разбор содержимого quick.save."""
    try:
        res = read_file(data)
    except Exception as exc:
        raise SaveReadError(f"Ошибка парсинга quick.save: {exc}") from exc

    records = res.get("records", [])
    state = next((r for r in records if r.get("type") == 56), None)
    counts = {k: state["int"].get(k) for k in ("members", "squads")} if state and "int" in state else {}
    mods = [x["sid"] for x in state["refs"].get("mods", [])] if state and "refs" in state else []

    # Сырые и нормализованные ключи мира
    st_int = state.get("int", {}) if state else {}
    world = {
        "day": st_int.get("time day"),
        "hour": st_int.get("time hour"),
        "minute": st_int.get("time minute"),
        "money": st_int.get("player money"),
        "raw": {k: st_int.get(k) for k in ("player money", "time day", "time hour", "time minute") if k in st_int},
    }
    research_rec = next((r for r in records if r.get("type") == 21), None)

    return {
        "header": res.get("header"),
        "consumed": res.get("consumed"),
        "size": res.get("size"),
        "registry": parse_registry(records),
        "player_counts": counts,
        "mods": mods,
        "world": world,
        "research": parse_research(research_rec),
        "towns": parse_towns(records),
        "diplomacy_raw": parse_diplomacy(records),
        "uniques_raw": parse_uniques(state),
        "diplomacy": extract_diplomacy(records, faction_sid, gamedb_inst),
        "world_state": extract_world_state(records, gamedb_inst),
    }


def read_quick_save(
    save_dir: PathLike,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    gamedb_inst: Any = None,
) -> dict[str, Any]:
    """Читает и парсит quick.save из каталога сохранения."""
    p = Path(save_dir) / "quick.save"
    if not p.is_file():
        raise SaveReadError(f"Файл quick.save не найден: {p}")
    return parse_quick_save(p.read_bytes(), faction_sid, gamedb_inst)


# -----------------------------------------------------------------------------
# Чтение персонажей, отрядов и инвентаря из platoon/
# -----------------------------------------------------------------------------

def collect_items(
    cont_sid: str,
    by_sid: dict[str, dict[str, Any]],
    container_label: str = "inventory",
    seen: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Рекурсивный сбор предметов из контейнера (тип 41) и вложенных рюкзаков."""
    if seen is None:
        seen = set()
    out: list[dict[str, Any]] = []
    cont = by_sid.get(cont_sid)
    if not cont or cont.get("type") != 41:
        return out
    for inst in cont.get("instances", []):
        it = by_sid.get(inst.get("target", ""))
        if not it or it.get("type") != 42 or it.get("sid") in seen:
            continue
        sid = it.get("sid", "")
        seen.add(sid)
        s, f, i, b = it.get("string", {}), it.get("float", {}), it.get("int", {}), it.get("bool", {})
        item_entry = {
            "instance_id": str(inst.get("id")) if inst.get("id") is not None else None,
            "item_sid": sid,
            "container": container_label,
            "section": s.get("section", ""),
            "base_data_sid": s.get("base data sid", ""),
            "material_sid": s.get("material sid", ""),
            "color_sid": s.get("color sid", ""),
            "company_sid": s.get("company sid", ""),
            "quality": _r(f.get("quality")),
            "charges": _r(f.get("charges")),
            "wear": _r(f.get("wear")) if "wear" in f else None,
            "dam": _r(f.get("dam")) if "dam" in f else None,
            "stun": _r(f.get("stun")) if "stun" in f else None,
            "level": i.get("level"),
            "quantity": i.get("quantity"),
            "inventory_xy": [i.get("inventory x"), i.get("inventory y")],
            "item_function": i.get("item function"),
            "unique": b.get("unique") if "unique" in b else None,
            "owned_by": clean_handle(town_handle(i, "ownedby")),
            # «Украдено»: владелец предмета - другой персонаж; признак нужен после исключения raw из публикации.
            "is_stolen": i.get("ownedbyTYPE") == 1,
            "raw": {"bool": dict(b), "float": dict(f), "int": dict(i), "string": dict(s)},
        }
        out.append(item_entry)
        # Если предмет является рюкзаком с вложенным контейнером 41
        for sub in it.get("instances", []):
            sub_target = sub.get("target", "")
            if sub_target in by_sid and by_sid[sub_target].get("type") == 41:
                out += collect_items(sub_target, by_sid, f"{container_label}>{sid}", seen)
    return out


def item_game_fields(sid: str | None, gamedb_inst: Any) -> dict[str, Any]:
    """Имя и базовая цена шаблона предмета из индекса игры: `value` итоговой записи после слияния модов."""
    entry = gamedb_inst.get(sid) if isinstance(gamedb_inst, dict) and sid else None
    if not isinstance(entry, (list, tuple)) or len(entry) < 5:
        return {}
    stats = entry[4] or {}
    out: dict[str, Any] = {"name": entry[1] or entry[0], "name_en": entry[0]}
    if "value" in stats:
        out |= {"base_price": stats["value"], "base_price_source": stats.get("value_source")}
    return out


def parse_limbs(
    med_rec: dict[str, Any] | None,
    by_sid: dict[str, dict[str, Any]],
    who: str,
    warnings: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Считывает протезы (refs 'limbs') и потерянные конечности (int 'limbs') из записи 57."""
    if not med_rec:
        return {}, []

    med_int = med_rec.get("int", {})
    limbs_mask = med_int.get("limbs", 0)
    # 2 бита на конечность: 0=своя, 1=потеряна, 2=протез
    states = {name: (limbs_mask >> (2 * i)) & 3 for i, name in enumerate(LIMBS) if (limbs_mask >> (2 * i)) & 3}

    installed: dict[str, dict[str, Any]] = {}
    for ref in med_rec.get("refs", {}).get("limbs", []):
        it = by_sid.get(ref.get("sid", ""))
        idx = ref["v"][0] if ref.get("v") else None
        if it is None or it.get("type") != 42 or not isinstance(idx, int) or not 0 <= idx < len(LIMBS):
            warnings.append(f"'{who}': ссылка на протез {ref.get('sid')} не разобрана")
            continue
        limb_name = LIMBS[idx]
        it_s, it_i = it.get("string", {}), it.get("int", {})
        lvl = it_i.get("level")
        installed[limb_name] = {
            "item_sid": it.get("sid"),
            "base_data_sid": it_s.get("base data sid"),
            "level": lvl,
            "grade": CONFIRMED_GRADES.get(lvl) if lvl is not None else None,
        }

    lost: list[str] = [
        limb for limb in LIMBS
        if states.get(limb) == 1 and limb not in installed
    ]
    return installed, lost


def clean_handle(h: Sequence[int | None] | None) -> list[int | None] | None:
    """Очищает безадресные (TYPE=11) или нулевые handle (TYPE, C, S, I, CS)."""
    if not h:
        return None
    # TYPE 11 означает безадресную глобальную задачу (JOB_MEDIC, JOB_REPAIR_ROBOT)
    if h[0] == 11:
        return None
    # Если все C, S, I, CS нулевые или отсутствуют
    if all(v in (0, None) for v in h[1:]):
        return None
    return list(h)


clean_job_handle = clean_handle


def parse_bounties(st_str: dict[str, Any], st_int: dict[str, Any]) -> dict[str, Any]:
    """Извлекает награды за голову персонажа (bountyfac<idx>, bountyexp<idx>, crimes<idx>)."""
    bounty_nums = sorted({
        int(k[len("bountyexp"):])
        for k in st_int
        if k.startswith("bountyexp") and k[len("bountyexp"):].isdigit()
    } | {
        int(k[len("bountyfac"):])
        for k in st_str
        if k.startswith("bountyfac") and k[len("bountyfac"):].isdigit()
    })

    entries: list[dict[str, Any]] = []
    for num in bounty_nums:
        fac = st_str.get(f"bountyfac{num}")
        amount = st_int.get(f"bountyexp{num}")
        crimes = st_int.get(f"crimes{num}")
        entries.append({
            "index": num,
            "faction_sid": fac,
            "amount": amount,
            "crimes": crimes,
        })

    has_bounty = any(b["amount"] is not None and b["amount"] > 0 for b in entries)
    total_amount = sum(b["amount"] for b in entries if b["amount"])
    return {
        "has_bounty": has_bounty,
        "total_amount": total_amount,
        "entries": entries,
    }


def decode_jobs(jobs_rec: dict[str, Any] | None) -> dict[str, Any]:
    """Декодирует очередь постоянных (pjob) и временных (tjob) работ персонажа."""
    if not jobs_rec:
        return {"enabled": None, "primary": [], "temporary": [], "raw": {}}
    j_int, j_str, j_bool = jobs_rec.get("int", {}), jobs_rec.get("string", {}), jobs_rec.get("bool", {})

    primary: list[dict[str, Any]] = []
    p_nums = sorted({
        int(k[len("pjob"):])
        for k in j_int
        if k.startswith("pjob") and k[len("pjob"):].isdigit()
    })
    for num in p_nums:
        code = j_int.get(f"pjob{num}")
        primary.append({
            "index": num,
            "task_code": code,
            "task_name": get_task_name(code),
            "target_handle": clean_job_handle(town_handle(j_int, f"pjobT{num}")),
        })

    temporary: list[dict[str, Any]] = []
    t_nums = sorted({
        int(k[len("tjob"):])
        for k in j_int
        if k.startswith("tjob") and k[len("tjob"):].isdigit()
    })
    for num in t_nums:
        code = j_int.get(f"tjob{num}")
        temporary.append({
            "index": num,
            "task_code": code,
            "task_name": get_task_name(code),
            "target_handle": clean_job_handle(town_handle(j_int, f"tjobT{num}")),
        })

    return {
        "enabled": j_bool.get("jobs"),
        "primary": primary,
        "temporary": temporary,
        "raw": {"int": dict(j_int), "string": dict(j_str), "bool": dict(j_bool)},
    }


def parse_character(
    inst: dict[str, Any],
    by_sid: dict[str, dict[str, Any]],
    squad_meta: dict[str, Any],
    warnings: list[str] | None = None,
    gamedb_inst: Any = None,
) -> dict[str, Any] | None:
    """Извлекает полный профиль персонажа из инстанса записи типа 30."""
    warn_list = warnings if warnings is not None else []
    linked = {by_sid[s]["type"]: by_sid[s] for s in inst.get("states", []) if s in by_sid}
    st = linked.get(36)    # CharacterState
    stats = linked.get(25) # CharacterStats
    med = linked.get(57)   # MedicalState
    look = linked.get(66)  # CharacterAppearance
    inv = linked.get(41)   # Container
    jobs_rec = linked.get(67) # JobsState

    if not st:
        return None

    # Имя персонажа
    char_name = st.get("string", {}).get("name") or (stats or {}).get("name") or ""

    # Стабильный ID формируется строго из portrait_serial (или handle)
    serial = st.get("int", {}).get("portrait_serial")
    if serial is not None:
        char_id = f"ps:{serial}"
    else:
        char_id = f"inst:{inst.get('id')}"
        warn_list.append(f"У персонажа '{char_name}' нет portrait_serial; ID: {char_id}")

    # Принадлежность фракции
    owner_faction = st.get("string", {}).get("owner faction ID", "")

    # Базовые навыки и атрибуты с маппингом на канонические имена FCS
    raw_stats = dict(stats.get("float", {})) if stats else {}
    attributes_base = {
        SKILL_NAMES[k]: _r(raw_stats[k])
        for k in ATTRIBUTES if k in raw_stats and k in SKILL_NAMES
    }
    skills_base = {
        SKILL_NAMES[k]: _r(raw_stats[k])
        for k in STAT_UI
        if k in raw_stats and k in SKILL_NAMES and k not in ATTRIBUTES
    }
    hidden_stats_raw = {
        k: _r(v)
        for k, v in raw_stats.items() if k not in SKILL_NAMES
    }

    # Протезы и потерянные конечности
    installed_limbs, lost_limbs = parse_limbs(med, by_sid, char_name, warn_list)

    # Медицинское состояние
    health_info: dict[str, Any] = {}
    if med:
        parts: list[dict[str, Any]] = []
        med_s, med_f, med_b = med.get("string", {}), med.get("float", {}), med.get("bool", {})
        for idx in range(16):
            sid_field = f"sid{idx}"
            hit_field = f"hit{idx}"
            if sid_field not in med_s and hit_field not in med_f:
                continue
            part_sid = med_s.get(sid_field, "")
            part_dict: dict[str, Any] = {
                "index": idx,
                "part_sid": part_sid,
            }
            # Сопоставляем протез или потерю по каноническому SID, GameDB или имени
            limb_id = BASE_LIMB_SIDS.get(part_sid)
            if not limb_id and gamedb_inst:
                g_entry = gamedb_inst.get(part_sid) if isinstance(gamedb_inst, dict) else None
                en_name = (g_entry[0] if g_entry else "").lower()
                for l_name, l_pname in [("left_arm", "left arm"), ("right_arm", "right arm"), ("left_leg", "left leg"), ("right_leg", "right leg")]:
                    if l_pname in en_name:
                        limb_id = l_name
                        break
            if not limb_id:
                p_lower = part_sid.lower()
                for l_name, l_pname in [("left_arm", "left arm"), ("right_arm", "right arm"), ("left_leg", "left leg"), ("right_leg", "right leg")]:
                    if l_pname in p_lower:
                        limb_id = l_name
                        break

            if limb_id:
                if limb_id in installed_limbs:
                    part_dict["limb"] = "prosthetic"
                elif limb_id in lost_limbs:
                    part_dict["limb"] = "lost"

            for k in HEALTH_FIELDS:
                val = med_f.get(f"{k}{idx}")
                if val is not None:
                    part_dict[k] = _r(val)
            parts.append(part_dict)

        health_info = {
            "flags": med_b,
            "blood": _r(med_f.get("blood")),
            "hunger": _r(med_f.get("hung"), 4),
            "KO": _r(med_f.get("KO")),
            "bleeding": _r(med_f.get("bleeding")),
            "fed": _r(med_f.get("fed")),
            "parts": parts,
        }

    # Внешность, раса, пол
    appearance_info: dict[str, Any] = {}
    if look:
        race_refs = look.get("refs", {}).get("race", [])
        race_sid = race_refs[0].get("sid") if race_refs else None
        appearance_info = {
            "race_sid": race_sid,
            "female": look.get("bool", {}).get("sex female", False),
            "age": look.get("int", {}).get("Age"),
        }

    # Инвентарь и экипировка
    items: list[dict[str, Any]] = []
    if inv:
        items = collect_items(inv.get("sid", ""), by_sid, "inventory")

    # Раскладка по слотам экипировки
    weapons: list[dict[str, Any]] = []
    armour: dict[str, Any] = {s: None for s in ("head", "shirt", "body", "legs", "boots")}
    backpack_item: dict[str, Any] | None = None
    backpack_items: list[dict[str, Any]] = []
    belt_item: dict[str, Any] | None = None
    inventory_items: list[dict[str, Any]] = []

    for it in items:
        cont = it.get("container", "")
        sec = it.get("section", "")
        if cont.startswith("inventory>"):
            backpack_items.append(it)
            continue
        if cont != "inventory":
            continue
        if sec in ("weapon_back", "weapon_hip", "back", "hip"):
            w_item = dict(it)
            w_item["slot"] = "weapon_back" if sec in ("weapon_back", "back") else "weapon_hip"
            weapons.append(w_item)
        elif sec in ("head", "shirt", "body", "legs", "boots", "armour"):
            slot_name = "body" if sec == "armour" else sec
            armour[slot_name] = it
        elif sec == "belt":
            belt_item = it
        elif sec in ("backpack_attach", "backpack"):
            backpack_item = it
        elif sec == "main":
            inventory_items.append(it)

    # Очередь работ (тип 67)
    jobs_info = decode_jobs(jobs_rec)

    # Переноска, награды и боевые режимы из записи 36
    st_int = st.get("int", {})
    st_str = st.get("string", {})
    st_bool = st.get("bool", {})

    carrying_handle = {
        "carryingS": st_int.get("carryingS"),
        "carryingI": st_int.get("carryingI"),
        "carryingC": st_int.get("carryingC"),
        "carryingCS": st_int.get("carryingCS"),
        "carryingTYPE": st_int.get("carryingTYPE"),
    }
    is_carrying = bool(st_bool.get("carrying", False))
    valid_handle = clean_handle(town_handle(st_int, "carrying"))
    carrying_info = {
        "is_carrying": is_carrying,
        "target_handle": valid_handle if is_carrying else None,
        "carried_by": valid_handle if (not is_carrying and valid_handle and valid_handle[0] == 1) else None,
        "raw": carrying_handle,
    }

    bounties_info = parse_bounties(st_str, st_int)

    combat_modes = {
        "taunt": st_bool.get("taunt mode"),
        "passive": st_bool.get("passive mode"),
        "ranged": st_bool.get("ranged mode"),
        "defensive": st_bool.get("defensive mode"),
        "chase": st_bool.get("chase mode"),
        "stealth": st_bool.get("stealth"),
        "non_combat": st_bool.get("non combat mode"),
    }

    return {
        "id": char_id,
        "char_sid": inst.get("id"),
        "name": char_name,
        "owner_faction": owner_faction,
        "squad_id": squad_meta.get("squad_id"),
        "squad_name": squad_meta.get("squad_name"),
        "template_sid": inst.get("target"),
        "position": [round(v, 1) for v in inst.get("pos", [])] if inst.get("pos") else None,
        "rotation": [round(v, 4) for v in inst.get("rot", [])] if inst.get("rot") else None,
        "handle": town_handle(st_int, "handle"),
        "appearance": appearance_info,
        "attributes_base": attributes_base,
        "skills_base": skills_base,
        "hidden_stats_raw": hidden_stats_raw,
        "health": health_info,
        "prosthetics": installed_limbs,
        "lost_limbs": lost_limbs,
        "weapons": weapons,
        "armour": armour,
        "belt": belt_item,
        "backpack": backpack_item,
        "backpack_items": backpack_items,
        "inventory": inventory_items,
        "items": items,
        "jobs": jobs_info,
        "carrying": carrying_info,
        "bounties": bounties_info,
        "combat_modes": combat_modes,
        "state_flags": st_bool,
    }


def read_platoon(
    data_or_path: bytes | PathLike,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    gamedb_inst: Any = None,
    squad_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Разбирает .platoon и возвращает (squad_dict, characters_list)."""
    raw = data_or_path if isinstance(data_or_path, bytes) else Path(data_or_path).read_bytes()
    try:
        res = read_file(raw)
    except Exception as exc:
        raise SaveReadError(f"Ошибка парсинга .platoon: {exc}") from exc

    by_sid = {r["sid"]: r for r in res.get("records", [])}
    squad_meta = squad_meta or {}

    chars: list[dict[str, Any]] = []
    for rec in res.get("records", []):
        if rec.get("type") != 30:
            continue
        for inst in rec.get("instances", []):
            ch = parse_character(inst, by_sid, squad_meta, gamedb_inst=gamedb_inst)
            if ch and ch.get("owner_faction") == faction_sid:
                chars.append(ch)

    squad_info = dict(squad_meta)
    squad_info["members"] = [c["name"] for c in chars]
    squad_info["member_ids"] = [c["id"] for c in chars]
    return squad_info, chars


def read_all_platoons(
    save_dir: PathLike,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    quick_save_data: dict[str, Any] | None = None,
    gamedb_inst: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Читает все файлы platoon/ игрока в соответствии с реестром quick.save."""
    sdir = Path(save_dir)
    pdir = sdir / "platoon"
    if not pdir.is_dir():
        raise SaveReadError(f"Каталог platoon не найден: {pdir}")

    qs = quick_save_data or read_quick_save(sdir)
    registry = qs.get("registry", {})
    warnings: list[str] = []

    squads: list[dict[str, Any]] = []
    characters: list[dict[str, Any]] = []

    for stem, reg in sorted(registry.items(), key=lambda x: (x[1].get("squad_index") is None, x[1].get("squad_index"), x[0])):
        if reg.get("faction_sid") != faction_sid:
            continue
        pfile = pdir / f"{stem}.platoon"
        if not pfile.is_file():
            warnings.append(f"Файл отряда игрока отсутствует на диске: {pfile.name}")
            continue

        squad_meta = {
            "squad_id": stem,
            "squad_name": reg.get("name"),
            "squad_index": reg.get("squad_index"),
            "dead": reg.get("dead"),
        }
        sq_info, sq_chars = read_platoon(pfile, faction_sid, gamedb_inst, squad_meta)
        squads.append(sq_info)
        characters.extend(sq_chars)

    # Контроль дубликатов ID персонажей
    id_counts = Counter(c["id"] for c in characters)
    for cid, cnt in id_counts.items():
        if cnt > 1:
            warnings.append(f"Идентификатор {cid} встречается {cnt} раз(а) — возможен дубликат персонажа")

    return squads, characters, warnings


# -----------------------------------------------------------------------------
# Чтение недвижимости из zone/*.zone с иерархией интерьера
# -----------------------------------------------------------------------------

def read_player_property(
    save_dir: PathLike,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    towns: list[dict[str, Any]] | None = None,
    gamedb_inst: Any = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Сканирует zone/*.zone и извлекает постройки, интерьер и хранилища игрока с иерархией."""
    sdir = Path(save_dir)
    zdir = sdir / "zone"
    if not zdir.is_dir():
        return {"buildings": [], "player_towns": [], "zone_files": 0, "zone_files_with_property": []}

    needle = faction_sid.encode("utf-8")
    buildings: list[dict[str, Any]] = []
    zone_files_with_prop: list[str] = []
    all_zone_files = sorted(zdir.glob("*.zone"))

    town_map = {tuple(t["handle"]): t["name"] for t in (towns or []) if t.get("handle")}

    for zf in all_zone_files:
        try:
            raw = zf.read_bytes()
        except OSError:
            continue
        # Быстрый отбор: длина >= 12, сигнатура заголовка == 15 и наличие строки faction_sid
        if len(raw) < 12 or struct.unpack_from("<i", raw)[0] != 15 or needle not in raw:
            continue

        try:
            res = read_file(raw)
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"Ошибка парсинга зоны {zf.name}: {exc}")
            continue

        records = res.get("records", [])
        by_sid = {r["sid"]: r for r in records}

        # Карта state_sid -> инстанс записи 30
        inst_by_state: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.get("type") == 30:
                for x in r.get("instances", []):
                    for st_sid in x.get("states", []):
                        inst_by_state[st_sid] = x

        # Все записи состояния зданий (тип 35) в этой зоне
        states_35 = [r for r in records if r.get("type") == 35]
        by_handle: dict[tuple, dict[str, Any]] = {}
        for r in states_35:
            h = town_handle(r.get("int"), "handle")
            if h is not None:
                by_handle[tuple(h)] = r

        # Фильтруем здания игрока
        own_35 = [r for r in states_35 if r.get("string", {}).get("owner faction ID") == faction_sid]
        if not own_35:
            continue

        zone_stem = zf.name[len("zone."):-len(".zone")] if zf.name.startswith("zone.") else zf.stem

        # Формируем объекты зданий
        objs: dict[str, dict[str, Any]] = {}
        for r in own_35:
            sid = r.get("sid", "")
            inst = inst_by_state.get(sid)
            b_int = r.get("int", {})
            b_str = r.get("string", {})
            b_bool = r.get("bool", {})
            b_float = r.get("float", {})

            # Сбор хранилищ (тип 41) из states инстанса здания
            storage_items: list[dict[str, Any]] = []
            if inst:
                conts = [s for s in inst.get("states", []) if by_sid.get(s, {}).get("type") == 41]
                for c_sid in conts:
                    storage_items.extend(collect_items(c_sid, by_sid, f"storage>{c_sid}"))
                for it in storage_items:
                    it.update(item_game_fields(it.get("base_data_sid"), gamedb_inst))

            pos = [round(v, 1) for v in inst.get("pos", [])] if inst and inst.get("pos") else None

            template_sid = inst.get("target") if inst else None
            raw_name = b_str.get("name")
            if not raw_name or raw_name == "0":
                r_name = r.get("name")
                raw_name = r_name if r_name and r_name != "0" else None

            def _resolve_label(target_sid: str | None) -> str | None:
                if not target_sid or not isinstance(gamedb_inst, dict):
                    return None
                entry = gamedb_inst.get(target_sid)
                if entry and isinstance(entry, (list, tuple)):
                    return entry[1] or entry[0]
                if entry and isinstance(entry, dict):
                    return entry.get("name_ru") or entry.get("name")
                return None

            template_name = _resolve_label(template_sid)
            resolved_name = raw_name or template_name

            power_on = b_bool.get("power on")
            gets_battery = b_bool.get("gets battery")
            battery_charge = _r(b_float.get("batterycharge"))

            is_paused = b_bool.get("pause")
            is_repeat = b_bool.get("repeat")
            production_progress = _r(b_float.get("production amount"))
            consumption_data = {
                k: _r(v)
                for k, v in b_float.items()
                if k.startswith("consumption amount")
            }
            consumption = consumption_data if consumption_data else None
            resource_mult = _r(b_float.get("resource mult"))
            mining_resource_level = _r(b_float.get("mining resource level"))

            is_complete = b_bool.get("is complete")
            destroyed = b_bool.get("destroyed")
            broken = b_bool.get("broken")
            construction_progress_raw = _r(b_float.get("construction progress"))

            c_mats_keys = sorted(
                (int(k[:-len("construction mats")]), k)
                for k in b_float
                if k.endswith("construction mats") and k[:-len("construction mats")].isdigit()
            )
            construction_deposited = [_r(b_float[k], 4) for _, k in c_mats_keys] if c_mats_keys else None

            craft_indices = sorted(
                int(k[len("current"):])
                for k in b_str
                if k.startswith("current") and k[len("current"):].isdigit() and b_str[k]
            )
            craft_queue = [
                {
                    "item_sid": b_str[f"current{i}"],
                    "item_name": _resolve_label(b_str[f"current{i}"]),
                    "progress": _r(b_float.get(f"current prog{i}"), 4),
                    "crit": b_int.get(f"crit{i}"),
                }
                for i in craft_indices
            ] or None

            partial_indices = sorted(
                int(k[len("craft partial item sid "):])
                for k in b_str
                if k.startswith("craft partial item sid ") and k[len("craft partial item sid "):].isdigit() and b_str[k]
            )
            craft_partial_items = [
                {
                    "item_sid": b_str[f"craft partial item sid {i}"],
                    "item_name": _resolve_label(b_str[f"craft partial item sid {i}"]),
                    "amount": _r(b_float.get(f"craft partial item val {i}"), 4),
                }
                for i in partial_indices
            ] or None

            current_shots = b_int.get("current shots")
            is_locked = b_bool.get("locked")
            lock_level = b_int.get("lock lvl")
            hardness = _r(b_float.get("hardness"))

            usage_nodes = [
                v for v in indexed(b_str, "usagenode_") if v
            ]

            building_town_handle = town_handle(b_int, "town")
            objs[sid] = {
                "id": inst.get("id") if inst else sid,
                "sid": sid,
                "name": resolved_name,
                "template_name": template_name,
                "zone": zone_stem,
                "template_sid": template_sid,
                "position": pos,
                "complete": is_complete,
                "destroyed": destroyed,
                "broken": broken,
                "construction_progress_raw": construction_progress_raw,
                "construction_deposited": construction_deposited,
                "power_on": power_on,
                "gets_battery": gets_battery,
                "battery_charge": battery_charge,
                "is_paused": is_paused,
                "is_repeat": is_repeat,
                "production_progress": production_progress,
                "craft_queue": craft_queue,
                "craft_partial_items": craft_partial_items,
                "consumption": consumption,
                "resource_mult": resource_mult,
                "mining_resource_level": mining_resource_level,
                "current_shots": current_shots,
                "is_locked": is_locked,
                "lock_level": lock_level,
                "hardness": hardness,
                "usage_nodes": usage_nodes,
                "handle": town_handle(b_int, "handle"),
                "town": town_map.get(tuple(building_town_handle)) if building_town_handle else None,
                "storage_items": storage_items,
                "interior": [],
            }

        # Строим иерархию (вкладываем объекты интерьера в родительское здание)
        top_buildings: list[dict[str, Any]] = []
        for r in own_35:
            sid = r.get("sid", "")
            o = objs[sid]
            is_inside = r.get("bool", {}).get("is inside building", False)
            parent_handle = town_handle(r.get("int"), "isInsideBuilding") if is_inside else None
            parent_rec = by_handle.get(tuple(parent_handle)) if parent_handle else None

            if parent_rec and parent_rec.get("sid") in objs:
                # Вкладываем в родительское здание
                o["floor"] = r.get("int", {}).get("floornum")
                objs[parent_rec["sid"]]["interior"].append(o)
            else:
                top_buildings.append(o)

        if top_buildings:
            zone_files_with_prop.append(zf.name)
            buildings.extend(top_buildings)

    player_towns = sorted(t["name"] for t in (towns or []) if faction_sid in t.get("factions_here", []))

    return {
        "buildings": buildings,
        "player_towns": player_towns,
        "zone_files": len(all_zone_files),
        "zone_files_with_property": zone_files_with_prop,
    }


# -----------------------------------------------------------------------------
# Проверка целостности и строгая валидация
# -----------------------------------------------------------------------------

def check_save_inputs(save_dir: PathLike, faction_sid: str = DEFAULT_PLAYER_FACTION) -> None:
    """Строгая проверка обязательных файлов и структуры сохранения."""
    sdir = Path(save_dir)
    qs_path = sdir / "quick.save"
    if not qs_path.is_file():
        raise SaveReadError(f"Отсутствует обязательный файл quick.save в {sdir}")

    try:
        qs_bytes = qs_path.read_bytes()
        quick = read_file(qs_bytes)
    except Exception as exc:
        raise SaveReadError(f"Поврежден заголовок или структура quick.save: {exc}") from exc

    if not any(r.get("type") == 56 for r in quick.get("records", [])):
        raise SaveReadError("Повреждена обязательная структура quick.save: отсутствует запись 56")

    pdir = sdir / "platoon"
    if not pdir.is_dir():
        raise SaveReadError(f"Отсутствует обязательный каталог platoon в {sdir}")

    registry = parse_registry(quick.get("records", []))
    for stem, item in sorted(registry.items()):
        if Path(stem).name != stem or "/" in stem or "\\" in stem or ":" in stem or ".." in stem:
            raise SaveReadError(f"Некорректный путь platoon в реестре: {stem}")
        path = pdir / f"{stem}.platoon"
        # Проверяем обязательное наличие и целостность файлов отрядов игрока
        if item.get("faction_sid") == faction_sid:
            if not path.is_file():
                raise SaveReadError(f"Отсутствует обязательный .platoon игрока: {path.name}")
            try:
                parsed = read_file(path.read_bytes())
                if parsed["consumed"] != parsed["size"] or parsed["header"]["count"] < 0:
                    raise SaveReadError(f"Повреждена обязательная структура .platoon игрока: {path.name}")
            except Exception as exc:
                raise SaveReadError(f"Ошибка чтения .platoon игрока {path.name}: {exc}") from exc


# -----------------------------------------------------------------------------
# Высокоуровневый фасад полного снимка сохранения
# -----------------------------------------------------------------------------

def read_save_snapshot(
    save_dir: PathLike,
    faction_sid: str = DEFAULT_PLAYER_FACTION,
    gamedb_inst: Any = None,
) -> dict[str, Any]:
    """Считывает полное состояние сохранения: quick.save, отряды игрока, здания и мир."""
    sdir = Path(save_dir).resolve()
    check_save_inputs(sdir, faction_sid)

    qs_data = read_quick_save(sdir, faction_sid, gamedb_inst)
    squads, characters, warnings = read_all_platoons(sdir, faction_sid, qs_data, gamedb_inst)
    prop_data = read_player_property(sdir, faction_sid, qs_data.get("towns"), gamedb_inst, warnings)

    from tools.world_context import enrich_world_and_production_context
    enrich_world_and_production_context(
        characters,
        prop_data,
        (qs_data.get("world_state") or {}).get("unique_npcs"),
    )

    saved_at = datetime.datetime.fromtimestamp(
        (sdir / "quick.save").stat().st_mtime,
        tz=datetime.timezone.utc,
    )

    save_kind = (
        "autosave" if sdir.name.startswith("autosave")
        else "quicksave" if sdir.name == "quicksave"
        else "manual"
    )

    # Не сохраняем абсолютный локальный путь пользователя (AGENTS.md правило 9)
    return {
        "save": {
            "name": sdir.name,
            "path": sdir.name,
            "saved_at": saved_at.isoformat(),
            "kind": save_kind,
        },
        "world": qs_data.get("world", {}),
        "player_counts": qs_data.get("player_counts", {}),
        "mods": qs_data.get("mods", []),
        "squads": squads,
        "characters": characters,
        "research": qs_data.get("research"),
        "towns": qs_data.get("towns", []),
        "property": prop_data,
        "diplomacy_raw": qs_data.get("diplomacy_raw", []),
        "uniques_raw": qs_data.get("uniques_raw", []),
        "diplomacy": qs_data.get("diplomacy"),
        "world_state": qs_data.get("world_state"),
        "warnings": warnings,
    }
