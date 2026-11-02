"""Сравнение двух неизменяемых снимков (kenshi-session-diff/5).

Устойчивый ID сохраняется во всех элементах изменений.
Участники с одинаковым именем сортируются строго по (name, id).
Сравнение transfers агрегируется по (sid, char_id), имена служат метками.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from tools.index import DEFAULT_INDEX_PATH, check_branch_compatibility, load_index
from tools.io_utils import ExtractionError, canonical, digest, file_hash, linked, read_json

ROOT = Path(__file__).resolve().parents[1]

DIFF_FORMAT = "kenshi-session-diff/5"
COMPATIBLE = {
    "kenshi-session/2", "kenshi-session/3", "kenshi-session/4", "kenshi-session/5",
    "kenshi-session/6", "kenshi-session/7", "kenshi-session/8", "kenshi-session/9",
    "kenshi-session/10",
}
LIMBS_SINCE = 7
NUTRITION_SINCE = 10
LIMBS = ["left_arm", "right_arm", "left_leg", "right_leg"]
EPS = 0.01

TRACKED: dict[str, dict[str, Any]] = {
    "roster": {
        "fields": "состав по устойчивому id; name; squad_id; status.dead/unconscious/coma/incapacitated",
        "threshold": "точное совпадение",
    },
    "skills": {
        "fields": "attributes_base, skills_base, hidden_stats_raw",
        "threshold": f"|Δ| ≥ {EPS}",
    },
    "equipment": {
        "fields": "weapons по слоту (sid, grade, manufacturer, level); armour по слоту (sid, level); рюкзак (sid); пояс (sid, с v7)",
        "threshold": "точное совпадение",
    },
    "prosthetics": {
        "fields": f"по конечности: протез (sid, level) или 'lost' (lost_limbs); только если оба снимка v{LIMBS_SINCE}+",
        "threshold": "точное совпадение",
    },
    "items": {
        "fields": "quantity по (персонаж: id, место: inventory|backpack, sid)",
        "threshold": "≠ 0",
    },
    "transfers": {
        "fields": "sid, у которого количество у одних персонажей (по ID) выросло, у других уменьшилось",
        "threshold": "≠ 0",
    },
    "supplies": {
        "fields": "supplies.<категория>.total отряда; supplies.<категория>.nutrition (только v10+)",
        "threshold": "≠ 0",
    },
    "health": {
        "fields": "condition.blood/hunger_raw/KO/bleeding; health[часть].flesh/bandage/wear/stun",
        "threshold": f"|Δ| ≥ {EPS}",
    },
    "world": {
        "fields": "world.day, world.time, world.money",
        "threshold": "точное совпадение",
    },
    "not_tracked": {
        "fields": "research, property, position (координаты подключаются гео-профилем), meta, warnings",
        "threshold": "—",
    },
}


def _num_changes(a: dict[str, Any], b: dict[str, Any], counter: Counter, key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in sorted(a.keys() | b.keys()):
        x, y = a.get(k), b.get(k)
        counter[key] += 1
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if abs(y - x) >= EPS - 1e-9:
                out[k] = {"from": x, "to": y, "delta": round(y - x, 2)}
        elif x != y:
            out[k] = {"from": x, "to": y}
    return out


def _weapon_label(w: dict[str, Any] | None) -> Any:
    if not w:
        return None
    return {k: w.get(k) for k in ("name", "sid", "grade", "manufacturer", "level") if w.get(k) is not None}


def _armour_label(a: dict[str, Any] | None) -> Any:
    if not a:
        return None
    return {k: a.get(k) for k in ("name", "sid", "level") if a.get(k) is not None}


def _identity(label: Any) -> Any:
    if isinstance(label, list):
        return sorted(json.dumps(_identity(x), sort_keys=True) for x in label)
    return {k: v for k, v in label.items() if k != "name"} if isinstance(label, dict) else label


def _weapons_by_slot(c: dict[str, Any]) -> dict[str, Any]:
    slots: dict[str, list] = defaultdict(list)
    for w in c.get("weapons", []):
        slot = w.get("slot") or w.get("section") or "unslotted"
        slots[slot].append(_weapon_label(w))
    return {
        s: (v[0] if len(v) == 1 else sorted(v, key=lambda d: json.dumps(d, ensure_ascii=False)))
        for s, v in slots.items()
    }


def _version(s: dict[str, Any]) -> int:
    try:
        return int(str(s.get("format", "")).rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return 0


def _limbs(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {x: "lost" for x in c.get("lost_limbs", [])}
    for p in c.get("prosthetics", []):
        out[p["limb"]] = {k: p.get(k) for k in ("name", "sid", "level") if p.get(k) is not None}
    return out


def _quantities(c: dict[str, Any]) -> dict[tuple[str, str], int]:
    q: dict[tuple[str, str], int] = Counter()
    for row in c.get("inventory", []):
        q[("inventory", row["sid"])] += row["quantity"]
    for row in (c.get("backpack") or {}).get("contents", []):
        q[("backpack", row["sid"])] += row["quantity"]
    return q


def _names(*snaps: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in snaps:
        for c in s.get("characters", []):
            for row in c.get("inventory", []) + (c.get("backpack") or {}).get("contents", []):
                out[row["sid"]] = row.get("name") or row["sid"]
    return out


def _label(names: dict[str, str], sid: str) -> str:
    dup = [s for s, nm in names.items() if nm == names.get(sid)]
    return names.get(sid, sid) if len(dup) <= 1 else f"{names[sid]} [{sid}]"


def _char_label(char_names: dict[str, str], cid: str) -> str:
    name = char_names.get(cid) or cid
    dup = [i for i, nm in char_names.items() if nm == name]
    return name if len(dup) <= 1 else f"{name} [{cid}]"


def _health(a: dict[str, Any], b: dict[str, Any], counter: Counter) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cond = _num_changes(a.get("condition", {}), b.get("condition", {}), counter, "health")
    if cond:
        out["condition"] = cond
    pa = {p["part"]: p for p in a.get("health", [])}
    pb = {p["part"]: p for p in b.get("health", [])}
    parts: dict[str, Any] = {}
    for part in sorted(pa.keys() | pb.keys()):
        x = {k: v for k, v in pa.get(part, {}).items() if k in ("flesh", "bandage", "wear", "stun")}
        y = {k: v for k, v in pb.get(part, {}).items() if k in ("flesh", "bandage", "wear", "stun")}
        ch = _num_changes(x, y, counter, "health")
        if ch:
            parts[part] = ch
    if parts:
        out["parts"] = parts
    return out


def _ref(s: dict[str, Any], file: str | None, snapshot_id: str | None = None) -> dict[str, Any]:
    save = s.get("save", {})
    return {
        "snapshot_id": snapshot_id,
        "file": file,
        "save": save.get("name"),
        "kind": save.get("kind"),
        "saved_at": save.get("saved_at"),
        "day": s.get("world", {}).get("day"),
        "time": s.get("world", {}).get("time"),
    }


def diff(
    prev: dict[str, Any] | None,
    cur: dict[str, Any],
    prev_file: str | None = None,
    cur_file: str | None = None,
    prev_id: str | None = None,
    cur_id: str | None = None,
    explain: bool = False,
    parent_snapshot_id: str | None = None,
    prev_branch_id: str | None = None,
    cur_branch_id: str | None = None,
    index_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    head: dict[str, Any] = {
        "format": DIFF_FORMAT,
        "to": _ref(cur, cur_file or cur.get("meta", {}).get("session_file"), cur_id),
    }

    base_not_compared = [
        "research: дерево исследований не сравнивается в kenshi-session-diff/5",
        "property: здания и собственность не сравниваются в kenshi-session-diff/5",
        "position: географические координаты подключаются профильным артефактом в задаче 02",
    ]

    # Non-baseline / первая публикация
    if prev is None:
        return head | {
            "from": None,
            "valid": False,
            "reason": "Первая публикация / снимок без baseline — сравнивать не с чем",
            "problems": ["Предыдущий снимок (baseline) не задан"],
            "summary": {},
            "compared": {},
            "not_compared": base_not_compared,
            "roster": {},
            "skills": [],
            "equipment": [],
            "prosthetics": [],
            "inventory": [],
            "transfers": [],
            "supplies": {},
            "health": [],
            "world": {},
        }

    head["from"] = _ref(prev, prev_file or prev.get("meta", {}).get("session_file"), prev_id)

    problems: list[str] = []

    # Проверка веток и совместимости предков
    if prev_id and cur_id and index_data is not None and isinstance(index_data, dict):
        compat, branch_reason = check_branch_compatibility(index_data, prev_id, cur_id)
        if not compat:
            problems.append(branch_reason or "different_branches")
    else:
        if prev_branch_id is not None and cur_branch_id is not None and prev_branch_id != cur_branch_id:
            problems.append("different_branches")
        if parent_snapshot_id is not None and prev_id is not None and prev_id != parent_snapshot_id:
            problems.append("different_branches")
            problems.append(f"несовпадение родителя: заявлен {parent_snapshot_id}, но сравнивается {prev_id}")

    if prev.get("format") not in COMPATIBLE or cur.get("format") not in COMPATIBLE:
        problems.append(f"разные форматы: {prev.get('format')} и {cur.get('format')}")

    if not prev.get("characters"):
        problems.append("from: нет персонажей")
    if not cur.get("characters"):
        problems.append("to: нет персонажей")

    # Проверка неубывания игрового времени
    prev_day = prev.get("world", {}).get("day")
    cur_day = cur.get("world", {}).get("day")
    prev_time = prev.get("world", {}).get("time")
    cur_time = cur.get("world", {}).get("time")
    if prev_day is not None and cur_day is not None:
        if cur_day < prev_day:
            problems.append("game_time_regression")
            problems.append(f"игровой день уменьшился: был {prev_day}, стал {cur_day} (возможно ветвление или откат)")
        elif cur_day == prev_day and prev_time is not None and cur_time is not None:
            if str(cur_time) < str(prev_time):
                problems.append("game_time_regression")
                problems.append(f"игровое время уменьшилось в пределах дня {cur_day}: было {prev_time}, стало {cur_time}")

    pa = {c["id"]: c for c in prev.get("characters", []) if "id" in c}
    ca = {c["id"]: c for c in cur.get("characters", []) if "id" in c}

    # Запрет склеивания разных ps:* по имени; смена устойчивого ID означает разные сущности.
    if pa and ca and not (pa.keys() & ca.keys()):
        problems.append("ни одного общего id — вероятно, разные игры или формат идентификаторов")

    if problems:
        if "different_branches" in problems:
            main_reason = "different_branches"
        elif "game_time_regression" in problems:
            main_reason = "game_time_regression"
        else:
            main_reason = "; ".join(problems)

        return head | {
            "valid": False,
            "reason": main_reason,
            "problems": problems,
            "summary": {},
            "compared": {},
            "not_compared": base_not_compared,
            "roster": {},
            "skills": [],
            "equipment": [],
            "prosthetics": [],
            "inventory": [],
            "transfers": [],
            "supplies": {},
            "health": [],
            "world": {},
        }

    n: Counter = Counter()
    roster: dict[str, list] = {
        "joined": [],
        "left": [],
        "renamed": [],
        "squad_changed": [],
        "status_changed": [],
    }

    # Сортировка по строгому ключу (name, id)
    for k in sorted(ca.keys() - pa.keys(), key=lambda cid: (str(ca[cid].get("name") or ""), str(cid))):
        c = ca[k]
        roster["joined"].append({"id": k, "name": c.get("name"), "squad_id": c.get("squad_id"), "race": c.get("race")})
    for k in sorted(pa.keys() - ca.keys(), key=lambda cid: (str(pa[cid].get("name") or ""), str(cid))):
        c = pa[k]
        roster["left"].append({"id": k, "name": c.get("name"), "squad_id": c.get("squad_id")})
    n["roster"] += len(pa.keys() | ca.keys())

    limbs_tracked = _version(prev) >= LIMBS_SINCE and _version(cur) >= LIMBS_SINCE
    skills: list[dict[str, Any]] = []
    equipment: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    limbs: list[dict[str, Any]] = []

    common_keys = sorted(
        ca.keys() & pa.keys(),
        key=lambda cid: (str(ca[cid].get("name") or ""), str(cid)),
    )

    for k in common_keys:
        a, b = pa[k], ca[k]
        who = b.get("name") or ""
        n["roster"] += 3

        if a.get("name") != b.get("name"):
            roster["renamed"].append({"id": k, "from": a.get("name"), "to": b.get("name")})
        if a.get("squad_id") != b.get("squad_id"):
            roster["squad_changed"].append({
                "id": k,
                "name": who,
                "from": a.get("squad_id"),
                "to": b.get("squad_id"),
                "to_name": b.get("squad_name"),
            })
        for f in sorted(a.get("status", {}).keys() | b.get("status", {}).keys()):
            n["roster"] += 1
            if a.get("status", {}).get(f) != b.get("status", {}).get(f):
                roster["status_changed"].append({
                    "id": k,
                    "name": who,
                    "field": f,
                    "from": a.get("status", {}).get(f),
                    "to": b.get("status", {}).get(f),
                })

        sk: dict[str, Any] = {}
        for grp in ("attributes_base", "skills_base", "hidden_stats_raw"):
            sk |= _num_changes(a.get(grp, {}), b.get(grp, {}), n, "skills")
        if sk:
            skills.append({"id": k, "name": who, "changes": sk})

        eq: dict[str, Any] = {}
        wa, wb = _weapons_by_slot(a), _weapons_by_slot(b)
        for slot in sorted(wa.keys() | wb.keys() | {"weapon_back", "weapon_hip"}):
            n["equipment"] += 1
            if _identity(wa.get(slot)) != _identity(wb.get(slot)):
                eq[slot] = {"from": wa.get(slot), "to": wb.get(slot)}
        for slot in sorted(a.get("armour", {}).keys() | b.get("armour", {}).keys()):
            n["equipment"] += 1
            x, y = _armour_label(a.get("armour", {}).get(slot)), _armour_label(b.get("armour", {}).get(slot))
            if _identity(x) != _identity(y):
                eq[slot] = {"from": x, "to": y}
        n["equipment"] += 1
        bx, by_ = (a.get("backpack") or {}).get("sid"), (b.get("backpack") or {}).get("sid")
        if bx != by_:
            eq["backpack"] = {"from": (a.get("backpack") or {}).get("name"), "to": (b.get("backpack") or {}).get("name")}
        if limbs_tracked:
            n["equipment"] += 1
            if (a.get("belt") or {}).get("sid") != (b.get("belt") or {}).get("sid"):
                eq["belt"] = {"from": (a.get("belt") or {}).get("name"), "to": (b.get("belt") or {}).get("name")}
            la, lb = _limbs(a), _limbs(b)
            lc: dict[str, Any] = {}
            for limb in LIMBS:
                n["prosthetics"] += 1
                if _identity(la.get(limb)) != _identity(lb.get(limb)):
                    lc[limb] = {"from": la.get(limb), "to": lb.get(limb)}
            if lc:
                limbs.append({"id": k, "name": who, "changes": lc})
        if eq:
            equipment.append({"id": k, "name": who, "changes": eq})

        h = _health(a, b, n)
        if h:
            health.append({"id": k, "name": who} | h)

    names = _names(prev, cur)
    char_names: dict[str, str] = {
        c["id"]: c.get("name") or c["id"]
        for c in prev.get("characters", []) + cur.get("characters", [])
        if "id" in c
    }

    per_sid: dict[str, dict[str, int]] = defaultdict(dict)
    all_char_ids = sorted(
        pa.keys() | ca.keys(),
        key=lambda cid: (str((ca.get(cid) or pa.get(cid)).get("name") or ""), str(cid)),
    )
    for k in all_char_ids:
        qa = _quantities(pa[k]) if k in pa else Counter()
        qb = _quantities(ca[k]) if k in ca else Counter()
        who = (ca.get(k) or pa.get(k)).get("name") or ""
        loc_delta: dict[str, dict[str, int]] = defaultdict(dict)
        for key in sorted(qa.keys() | qb.keys()):
            n["items"] += 1
            d = qb.get(key, 0) - qa.get(key, 0)
            if d:
                loc_delta[key[0]][_label(names, key[1])] = d
                per_sid[key[1]][k] = per_sid[key[1]].get(k, 0) + d
        if loc_delta:
            items.append({"id": k, "name": who} | dict(loc_delta))

    transfers: list[dict[str, Any]] = []
    for sid, by_char in sorted(per_sid.items()):
        gains = {_char_label(char_names, cid): d for cid, d in sorted(by_char.items()) if d > 0}
        losses = {_char_label(char_names, cid): d for cid, d in sorted(by_char.items()) if d < 0}
        if gains and losses:
            transfers.append({
                "item": _label(names, sid),
                "sid": sid,
                "from": losses,
                "to": gains,
                "party_net": sum(by_char.values()),
            })

    supplies: dict[str, Any] = {}
    sa, sb = prev.get("supplies", {}), cur.get("supplies", {})
    for cat in sorted(sa.keys() | sb.keys()):
        if cat == "artifacts":
            continue
        n["supplies"] += 1
        x = sa.get(cat, {}).get("total", 0)
        y = sb.get(cat, {}).get("total", 0)
        if x != y:
            supplies[cat] = {"from": x, "to": y, "delta": y - x}
        has_nutrition = "nutrition" in sa.get(cat, {}) or "nutrition" in sb.get(cat, {})
        if has_nutrition and _version(prev) >= NUTRITION_SINCE and _version(cur) >= NUTRITION_SINCE:
            n["supplies"] += 1
            x_nut = sa.get(cat, {}).get("nutrition", 0)
            y_nut = sb.get(cat, {}).get("nutrition", 0)
            if x_nut != y_nut:
                supplies[f"{cat}.nutrition"] = {"from": x_nut, "to": y_nut} | (
                    {"delta": round(y_nut - x_nut, 1)} if x_nut is not None and y_nut is not None else {}
                )

    world: dict[str, Any] = {}
    for f in ("day", "time", "money"):
        n["world"] += 1
        x, y = prev.get("world", {}).get(f), cur.get("world", {}).get(f)
        if x != y:
            world[f] = {"from": x, "to": y} | (
                {"delta": y - x} if isinstance(x, int) and isinstance(y, int) else {}
            )

    not_compared = list(base_not_compared)
    if not limbs_tracked:
        not_compared.append(f"протезы, потерянные конечности и пояс: в одном из снимков формат старше v{LIMBS_SINCE}")

    # Запрет ложных нулей изменений: если не было сравниваемых данных
    total_compared = sum(n.values())
    if total_compared == 0:
        return head | {
            "valid": False,
            "reason": "нет сравниваемых данных в отслеживаемых категориях",
            "problems": ["нет сравниваемых данных в отслеживаемых категориях"],
            "summary": {},
            "compared": {},
            "not_compared": not_compared,
            "roster": {},
            "skills": [],
            "equipment": [],
            "prosthetics": [],
            "inventory": [],
            "transfers": [],
            "supplies": {},
            "health": [],
            "world": {},
        }

    roster_filtered = {k: v for k, v in roster.items() if v}
    out = head | {
        "valid": True,
        "summary": {
            "characters_before": len(pa),
            "characters_after": len(ca),
            "common": len(pa.keys() & ca.keys()),
            "joined": len(roster_filtered.get("joined", [])),
            "left": len(roster_filtered.get("left", [])),
            "skill_changes": len(skills),
            "equipment_changes": len(equipment),
            "prosthetic_changes": len(limbs),
            "inventory_changes": len(items),
            "transfers": len(transfers),
            "health_changes": len(health),
            "supply_changes": len(supplies),
            "world_changes": len(world),
        },
        "compared": dict(sorted(n.items())),
        "not_compared": not_compared,
        "roster": roster_filtered,
        "skills": skills,
        "equipment": equipment,
        "prosthetics": limbs,
        "inventory": items,
        "transfers": transfers,
        "supplies": supplies,
        "health": health,
        "world": world,
    }
    if explain:
        out["tracked"] = TRACKED
    return out


def make_diff_id(prev_id: str | None, cur_id: str) -> str:
    p = prev_id or "baseline"
    return f"{p}__{cur_id}"


def store_diff(output_dir: Path, diff_id: str, diff_bytes: bytes) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{diff_id}.json"
    if target.exists():
        if linked(target) or target.is_dir() or target.read_bytes() != diff_bytes:
            raise ExtractionError(f"Существующий diff {diff_id} имеет другое содержимое или повреждён")
        return target
    temp_path = output_dir / f".tmp-{diff_id}.json"
    temp_path.write_bytes(diff_bytes)
    temp_path.rename(target)
    return target


def diff_snapshots(
    prev_dir: Path | None,
    cur_dir: Path,
    output_dir: Path | None = None,
    explain: bool = False,
    parent_snapshot_id: str | None = None,
    index_path: Path | None = None,
    index_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cur_raw = (cur_dir / "current.json").read_bytes()
    cur = read_json(cur_raw)
    cur_prov = read_json((cur_dir / "provenance.json").read_bytes()) if (cur_dir / "provenance.json").is_file() else {}
    cur_id = cur_prov.get("snapshot_id", cur_dir.name)
    cur_branch = cur_prov.get("branch_id")

    prev = None
    prev_id = None
    prev_branch = None
    if prev_dir is not None:
        if not prev_dir.is_dir():
            raise ExtractionError(f"Каталог предыдущего снимка не найден: {prev_dir}")
        prev_raw = (prev_dir / "current.json").read_bytes()
        prev = read_json(prev_raw)
        prev_prov = read_json((prev_dir / "provenance.json").read_bytes()) if (prev_dir / "provenance.json").is_file() else {}
        prev_id = prev_prov.get("snapshot_id", prev_dir.name)
        prev_branch = prev_prov.get("branch_id")

    effective_parent = parent_snapshot_id or cur_prov.get("parent_snapshot_id")

    idx_data = index_data
    if idx_data is None:
        if index_path is not None and index_path.is_file():
            idx_data = load_index(index_path)
        else:
            default_idx = ROOT / "data/index.json"
            if default_idx.is_file():
                try:
                    idx_data = load_index(default_idx)
                except Exception:
                    idx_data = None

    result = diff(
        prev=prev,
        cur=cur,
        prev_file=None,
        cur_file=None,
        prev_id=prev_id,
        cur_id=cur_id,
        explain=explain,
        parent_snapshot_id=effective_parent,
        prev_branch_id=prev_branch,
        cur_branch_id=cur_branch,
        index_data=idx_data,
    )

    if output_dir is not None:
        diff_id = make_diff_id(prev_id, cur_id)
        store_diff(output_dir, diff_id, canonical(result))

    return result
