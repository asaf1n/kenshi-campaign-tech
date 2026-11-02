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


def town_handle(i: dict[str, int] | None, prefix: str = "hand") -> tuple[int | None, ...] | None:
    """Извлекает 5-компонентный handle объекта/города (TYPE, C, S, I, CS)."""
    if not i:
        return None
    keys = ("TYPE", "C", "S", "I", "CS")
    vals = tuple(i.get(prefix + k) for k in keys)
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
    s, i = state_rec.get("string", {}), state_rec.get("int", {})
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
        pl_val = s.get(f"usedUniquesPlayer{num}")
        handle = town_handle(i, f"usedUniques{num}handle")
        uniques.append({
            "index": num,
            "sid": sid_val,
            "state_code": st_code,
            "player": pl_val,
            "handle": handle,
        })
    return uniques


def parse_quick_save(data: bytes) -> dict[str, Any]:
    """Полный разбор содержимого quick.save."""
    try:
        res = read_file(data)
    except Exception as exc:
        raise SaveReadError(f"Ошибка парсинга quick.save: {exc}") from exc

    state = next((r for r in res.get("records", []) if r.get("type") == 56), None)
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
    research_rec = next((r for r in res.get("records", []) if r.get("type") == 21), None)

    return {
        "header": res.get("header"),
        "consumed": res.get("consumed"),
        "size": res.get("size"),
        "registry": parse_registry(res.get("records", [])),
        "player_counts": counts,
        "mods": mods,
        "world": world,
        "research": parse_research(research_rec),
        "towns": parse_towns(res.get("records", [])),
        "diplomacy_raw": parse_diplomacy(res.get("records", [])),
        "uniques_raw": parse_uniques(state),
    }


def read_quick_save(save_dir: PathLike) -> dict[str, Any]:
    """Читает и парсит quick.save из каталога сохранения."""
    p = Path(save_dir) / "quick.save"
    if not p.is_file():
        raise SaveReadError(f"Файл quick.save не найден: {p}")
    return parse_quick_save(p.read_bytes())


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
            "item_sid": sid,
            "container": container_label,
            "section": s.get("section", ""),
            "base_data_sid": s.get("base data sid", ""),
            "material_sid": s.get("material sid", ""),
            "color_sid": s.get("color sid", ""),
            "company_sid": s.get("company sid", ""),
            "quality": f.get("quality"),
            "charges": f.get("charges"),
            "level": i.get("level"),
            "quantity": i.get("quantity"),
            "inventory_xy": [i.get("inventory x"), i.get("inventory y")],
            "item_function": i.get("item function"),
            "raw": {"bool": b, "float": f, "int": i, "string": s},
        }
        out.append(item_entry)
        # Если предмет является рюкзаком с вложенным контейнером 41
        for sub in it.get("instances", []):
            sub_target = sub.get("target", "")
            if sub_target in by_sid and by_sid[sub_target].get("type") == 41:
                out += collect_items(sub_target, by_sid, f"{container_label}>{sid}", seen)
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
    belt_item: dict[str, Any] | None = None
    inventory_items: list[dict[str, Any]] = []

    for it in items:
        sec = it.get("section", "")
        if it.get("container") != "inventory":
            continue
        if sec in ("weapon_back", "weapon_hip", "back", "hip"):
            weapons.append(it)
        elif sec in ("head", "shirt", "body", "legs", "boots", "armour"):
            slot_name = "body" if sec == "armour" else sec
            armour[slot_name] = it
        elif sec == "belt":
            belt_item = it
        elif sec == "backpack_attach":
            backpack_item = it
        elif sec == "main":
            inventory_items.append(it)

    # Очередь работ (тип 67)
    jobs_info: dict[str, Any] = {}
    if jobs_rec:
        jobs_info = {
            "int": dict(jobs_rec.get("int", {})),
            "string": dict(jobs_rec.get("string", {})),
        }

    # Переноска и награды из записи 36
    st_int = st.get("int", {})
    carrying_handle = {
        "carryingS": st_int.get("carryingS"),
        "carryingI": st_int.get("carryingI"),
        "carryingC": st_int.get("carryingC"),
        "carryingCS": st_int.get("carryingCS"),
        "carryingTYPE": st_int.get("carryingTYPE"),
    }
    bounties: dict[str, Any] = {
        "bountyfac": st.get("string", {}).get("bountyfac"),
        "bountyexp": st_int.get("bountyexp"),
        "crimes": st.get("string", {}).get("crimes"),
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
        "inventory": inventory_items,
        "items": items,
        "jobs": jobs_info,
        "carrying": carrying_handle,
        "bounties": bounties,
        "state_flags": st.get("bool", {}),
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

    town_map = {t["handle"]: t["name"] for t in (towns or []) if t.get("handle")}

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
                by_handle[h] = r

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

            pos = [round(v, 1) for v in inst.get("pos", [])] if inst and inst.get("pos") else None

            objs[sid] = {
                "id": inst.get("id") if inst else sid,
                "sid": sid,
                "name": r.get("name") or b_str.get("name"),
                "zone": zone_stem,
                "template_sid": inst.get("target") if inst else None,
                "position": pos,
                "complete": b_bool.get("is complete"),
                "destroyed": b_bool.get("destroyed"),
                "construction_progress_raw": _r(b_float.get("construction progress")),
                "handle": town_handle(b_int, "handle"),
                "town": town_map.get(town_handle(b_int, "town")),
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
            parent_rec = by_handle.get(parent_handle) if parent_handle else None

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

    qs_data = read_quick_save(sdir)
    squads, characters, warnings = read_all_platoons(sdir, faction_sid, qs_data, gamedb_inst)
    prop_data = read_player_property(sdir, faction_sid, qs_data.get("towns"), gamedb_inst, warnings)

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
        "warnings": warnings,
    }
