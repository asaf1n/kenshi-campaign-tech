"""Исследования и собственность игрока: запись 21 quick.save и здания из zone/*.zone.

Где лежат данные и что подтверждено — docs/save-format.md, разделы «Исследования» и «Собственность».
Формат блоков research / property — README.md, раздел «Режим сессии».
"""
import os
import re
from collections.abc import Callable
from typing import Any

try:
    from export_platoons import collect_items
    from kenshi_gd import read_file
    from saves import town_handle
except ImportError:
    from tools.export_platoons import collect_items
    from tools.kenshi_gd import read_file
    from tools.saves import town_handle

BUILDING_STATE = 35  # состояние здания: владелец, готовность, handle, ссылки на город и родительское здание
CONTAINER = 41
# Сводка содержимого контейнера (session_export.aggregate) — передаётся снаружи, чтобы не было циклического импорта.
Summarize = Callable[[list[dict[str, Any]], Any], list[dict[str, Any]]]

# Статусы блоков research / property (попадают в meta.verification); основание — docs/save-format.md.
VERIFICATION: dict[str, dict[str, Any]] = {
    "research.completed": {"status": "unverified", "source": "save",
                           "how": "finished# записи 21; длина = 'num finished' в 62 сохранениях; с экраном "
                                  "исследований не сверялось"},
    "research.paid": {"status": "unverified", "source": "save",
                      "how": "paid#; в 15 сохранениях с очередью вся очередь current# оплачена; оплаченное вне "
                             "очереди и не завершённое — в 2 из 62 сохранений (по 1); у исследований по чертежу "
                             "paid = false"},
    "research.in_progress/queue": {"status": "unverified", "source": "save",
                                   "how": "current# по порядку; в 15 сохранениях ненулевой прогресс только у current0"},
    "research.template": {"status": "template", "source": "template",
                          "how": "level, blueprint only, is level upgrade, enable buildings шаблона RESEARCH"},
    "research.tech_level": {"status": "unavailable", "how": "поля тех. уровня в сохранении нет; вычисление по "
                                                            "level_upgrades_completed с интерфейсом не сверено"},
    "property.buildings": {"status": "unverified", "source": "save",
                           "how": "'owner faction ID' записи 35 в zone/*.zone; дома в Хабе и Скуине, о которых "
                                  "сообщил игрок, найдены в 5 сохранениях; состав interior с интерфейсом не сверялся"},
    "property.storage": {"status": "unverified", "source": "save",
                         "how": "контейнер 41 из states инстанса здания, предметы 42 — как инвентарь персонажа"},
    "property.construction_progress_raw": {"status": "unverified", "source": "save",
                                           "how": "значение как есть; смысл не установлен (у готовых зданий 1–24)"},
}


def _label(n: dict[str, Any]) -> str | None:
    return (n.get("name_ru") or n.get("name") or None) if n.get("sid") else None


def _head(sid: str, names: Any) -> dict[str, Any]:
    """Название (ru как в игре), английское — если отличается; неразрешённый sid помечается."""
    n = names.name(sid)
    if n.get("name") is None:
        return {"sid": sid, "unresolved": True}
    out = {"name": _label(n), "sid": sid}
    if n.get("name") and n["name"] != out["name"]:
        out["name_en"] = n["name"]
    return out


# ---------------------------------------------------------------- исследования

def split_research_sid(sid: str, names: Any) -> tuple[str, dict[str, Any]]:
    """sid записи справочника и признаки составного sid исследования.

    '<sid исследования>.<N>' — уровень N повторяемого исследования (2 ≤ N ≤ 'repeats' шаблона);
    '<sid предмета>.TECH.<N>' — изученный чертёж предмета. Остальное — sid как есть.
    """
    if sid in names.ix:
        return sid, {}
    m = re.fullmatch(r"(.+)\.TECH\.(\d+)", sid)
    if m and m.group(1) in names.ix and names.type_of(m.group(1)) != "RESEARCH":
        return m.group(1), {"item_blueprint": True, "item_type": names.type_of(m.group(1))}
    m = re.fullmatch(r"(.+)\.(\d+)", sid)
    if m and names.type_of(m.group(1)) == "RESEARCH" and 2 <= int(m.group(2)) <= (names.stats(m.group(1)).get("repeats") or 0):
        return m.group(1), {"repeat_level": int(m.group(2))}
    return sid, {}


def research_entry(sid: str, status: str, paid: bool, names: Any, progress: float | None = None) -> dict[str, Any]:
    base, extra = split_research_sid(sid, names)
    t = names.stats(base) if not extra.get("item_blueprint") else {}
    head = _head(base, names)
    out = head | ({"sid": sid, "base_sid": base} if base != sid else {}) | extra | {"status": status, "paid": paid}
    if progress is not None:
        out["progress_raw"] = round(progress, 2)
    if "level" in t:
        out["level"] = t["level"]
    if t.get("blueprint only"):
        out["blueprint_only"] = True
    if t.get("is level upgrade"):
        out["level_upgrade"] = True
    # Открываемые постройки — у первого уровня; повторные уровни их не добавляют.
    unlocks = [_label(names.name(s)) or s for s in t.get("refs:enable buildings") or {}] if not extra else []
    if unlocks:
        out["unlocks"] = unlocks
    return out


def research_block(raw: dict[str, Any] | None, names: Any, warnings: list[str]) -> dict[str, Any]:
    """completed / in_progress / queue / paid из записи 21; статусы не выводятся из построенных зданий."""
    if raw is None:
        warnings.append("quick.save: запись исследований (тип 21) не найдена; research недоступен")
        return {"available": False}
    fin, paid, cur, prog = raw["finished"], raw["paid"], raw["current"], raw["current_progress"]
    paid_set, fin_set, cur_set = set(paid), set(fin), set(cur)
    for field, n, got in (("num finished", raw["num_finished"], len(fin)), ("num currents", raw["num_currents"], len(cur))):
        if n is not None and int(n) != got:
            warnings.append(f"quick.save, исследования: '{field}' = {n:g}, а в списке {got}")
    if len(prog) != len(cur):
        warnings.append(f"quick.save, исследования: {len(cur)} в очереди, но {len(prog)} значений прогресса")
    if fin_set & cur_set:
        warnings.append("quick.save, исследования: одно исследование одновременно завершено и в очереди")
    queue = [research_entry(s, "in_progress" if i == 0 else "queued", s in paid_set, names,
                            prog[i] if i < len(prog) else None) for i, s in enumerate(cur)]
    completed = [research_entry(s, "completed", s in paid_set, names) for s in fin]

    # Эффективный техуровень вычисляется по завершенным исследованиям level_upgrade (базовый 1)
    levels = [1]
    for e in completed:
        if e.get("level_upgrade"):
            lvl = e.get("level")
            if isinstance(lvl, int):
                levels.append(lvl + 1)
            name_en = e.get("name_en") or e.get("name") or ""
            m = re.search(r"Tech Level (\d+)", name_en, re.IGNORECASE)
            if m:
                levels.append(int(m.group(1)))
    tech_level = max(levels)

    blueprints_completed = [
        e.get("name") or e.get("sid")
        for e in completed
        if e.get("blueprint_only") or e.get("item_blueprint")
    ]

    return {
        "completed": completed,
        "in_progress": queue[:1],
        "queue": queue[1:],
        # Очередь в сохранениях всегда оплачена (paid у её записей); здесь — оплаченное вне очереди и не завершённое.
        "paid": [research_entry(s, "paid", True, names) for s in paid if s not in fin_set and s not in cur_set],
        "tech_level": tech_level,
        "level_upgrades_completed": [e.get("name") or e["sid"] for e in completed if e.get("level_upgrade")],
        "blueprints_completed": blueprints_completed,
    }


# ---------------------------------------------------------------- собственность

def _interior_slots(i: dict[str, int]) -> int:
    """Число объектов, которые здание считает своими: ключи 'internalbuildings-<N><TYPE|C|S|I|CS>'."""
    return len({k[len("internalbuildings-"):].rstrip("TYPECSI") for k in i if k.startswith("internalbuildings-")})


def _storage(inst: dict[str, Any] | None, by: dict[str, Any], names: Any, summarize: Summarize) -> list[dict[str, Any]] | None:
    """Содержимое контейнеров (тип 41), связанных с инстансом здания; None — у здания нет контейнера."""
    conts = [s for s in (inst or {}).get("states", []) if by.get(s, {}).get("type") == CONTAINER]
    if not conts:
        return None
    seen: set[str] = set()
    items = [it for c in conts for it in collect_items(c, by, names, "storage", seen)]
    # Пометка «Украдено» сверена только для инвентаря персонажей — у хранилищ не выводится.
    return [{k: v for k, v in row.items() if k != "stolen"} for row in summarize(items, names)]


def _object(rec: dict[str, Any], inst: dict[str, Any] | None, by: dict[str, Any], names: Any,
            summarize: Summarize) -> dict[str, Any]:
    tpl = (inst or {}).get("target")
    out: dict[str, Any] = {"id": (inst or {}).get("id") or rec["sid"], "name": rec["string"].get("name") or None}
    if tpl:
        head = _head(tpl, names)
        out |= {"template": head.get("name"), "template_sid": tpl}
        if head.get("unresolved"):
            out["unresolved"] = True
        cat = names.stats(tpl).get("building category")
        if cat:
            out["category"] = cat
    out |= {"complete": rec["bool"].get("is complete"), "destroyed": rec["bool"].get("destroyed"),
            "construction_progress_raw": round(rec["float"].get("construction progress", 0.0), 2)}
    storage = _storage(inst, by, names, summarize)
    if storage is not None:
        out["storage"] = storage
    return out


def zone_property(records: list[dict[str, Any]], zone: str, faction_sid: str, names: Any,
                  towns: dict[tuple, str], summarize: Summarize, warnings: list[str]) -> list[dict[str, Any]]:
    """Здания фракции в одной зоне: верхний уровень с вложенными interior.

    Шаблон и позиция — инстанс записи 30 ('0-buildinglist'), у которого states содержит sid состояния (35);
    вложенность — handle 'isInsideBuilding*' объекта = 'handle*' здания; город — 'town*' = 'hand*' записи 94.
    """
    by = {r["sid"]: r for r in records}
    inst = {s: x for r in records if r["type"] == 30 for x in r["instances"] for s in x["states"]}
    states = [r for r in records if r["type"] == BUILDING_STATE]
    by_handle = {town_handle(r["int"], "handle"): r for r in states}
    inside_count: dict[str, int] = {}
    for r in states:
        if r["bool"].get("is inside building"):
            p = by_handle.get(town_handle(r["int"], "isInsideBuilding"))
            if p is not None:
                inside_count[p["sid"]] = inside_count.get(p["sid"], 0) + 1
    own = [r for r in states if r["string"].get("owner faction ID") == faction_sid]
    objs = {r["sid"]: _object(r, inst.get(r["sid"]), by, names, summarize) for r in own}
    top: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (объект, местоположение)
    for r in own:
        o = objs[r["sid"]]
        if r["sid"] not in inst:
            warnings.append(f"{zone}: здание {r['sid']} без инстанса в списке зданий зоны — шаблон и позиция неизвестны")
        parent = by_handle.get(town_handle(r["int"], "isInsideBuilding")) if r["bool"].get("is inside building") else None
        if parent is not None and parent["sid"] in objs:
            o["floor"] = r["int"].get("floornum")
            objs[parent["sid"]].setdefault("interior", []).append(o)
            continue
        if r["bool"].get("is inside building"):
            if parent is None:
                o["inside_building"] = None
                warnings.append(f"{zone}: объект {o['id']} внутри здания, которое в зоне не найдено")
            else:
                pi = inst.get(parent["sid"]) or {}
                o["inside_building"] = {"id": pi.get("id") or parent["sid"],
                                        "template": _label(names.name(pi["target"])) if pi.get("target") else None,
                                        "owner_faction": parent["string"].get("owner faction ID")}
        x = (inst.get(r["sid"]) or {}).get("pos")
        top.append((o, {"town": towns.get(town_handle(r["int"], "town")), "zone": zone,
                        "position": [round(v, 1) for v in x] if x else None}))
    for r in own:
        declared, found = _interior_slots(r["int"]), inside_count.get(r["sid"], 0)
        if declared != found:
            warnings.append(f"{zone}: у здания {objs[r['sid']]['id']} {declared} объектов внутри по записи, "
                            f"найдено {found}")
    # interior дописывается по ходу цикла, поэтому порядок ключей собирается в конце.
    return [{"id": o["id"], "name": o["name"]} | loc | o for o, loc in top]


def collect_property(save_dir: str, faction_sid: str, names: Any, towns: list[dict[str, Any]],
                     summarize: Summarize, warnings: list[str]) -> dict[str, Any]:
    """Сканирует все zone/*.zone, а не только загруженную область: здания хранятся в файле своей зоны."""
    zdir = os.path.join(save_dir, "zone")
    files = sorted(f for f in os.listdir(zdir) if f.endswith(".zone")) if os.path.isdir(zdir) else []
    if not files:
        warnings.append("В папке сохранения нет zone/*.zone — собственность недоступна")
    town_names = {t["handle"]: t["name"] for t in towns}
    needle = faction_sid.encode("utf-8")
    buildings: list[dict[str, Any]] = []
    with_player = []
    for f in files:
        with open(os.path.join(zdir, f), "rb") as fh:
            data = fh.read()
        # sid фракции хранится строкой 'owner faction ID': без неё в зоне нет зданий игрока.
        if needle not in data:
            continue
        res = read_file(data)  # после записей идёт нерасшифрованный хвост, как у quick.save
        zone = f[len("zone."):-len(".zone")] if f.startswith("zone.") else f[:-len(".zone")]
        found = zone_property(res["records"], zone, faction_sid, names, town_names, summarize, warnings)
        if found:
            with_player.append(f)
            buildings += found
    player_towns = sorted(t["name"] for t in towns if faction_sid in t["factions_here"])
    owned_towns = {b.get("town") for b in buildings}
    for t in player_towns:
        if t not in owned_towns:
            warnings.append(f"Город «{t}» числит фракцию игрока (quick.save, запись 94), но здания игрока там "
                            "в zone/ не найдены")
    return {"buildings": buildings, "player_towns": player_towns,
            "source": {"zone_files": len(files), "zone_files_with_property": with_player}}
