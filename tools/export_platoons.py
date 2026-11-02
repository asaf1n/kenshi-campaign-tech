"""Экспорт персонажей и экипировки из сохранения Kenshi в JSON/CSV.

Полный экспорт:  python tools/export_platoons.py <platoon.zip | папка сохранения> <out_dir>
Режим сессии:    python tools/export_platoons.py --session [--save DIR] [--out DIR]
Справочник имён строится из установленной игры (см. gamedb.py).
"""
import argparse
import csv
import datetime
import json
import os
import struct
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamedb  # noqa: E402
from kenshi_gd import read_file  # noqa: E402
from saves import iter_entries, parse_quick_save  # noqa: E402

# Внутренние ключи навыков -> название в английском UI (по разделу [STATS] fcs.def).
# Ключи, которых в [STATS] нет, помечены "?": в интерфейсе игры они не показываются.
STAT_UI = {
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
    "climbing": "? (скрытый)", "tracking": "? (скрытый)", "mass combat": "? (скрытый)",
    "bluff": "? (скрытый)", "doctor": "? (скрытый)", "survival": "? (скрытый)",
    "warrior spirit": "? (скрытый)", "arrow defence": "? (скрытый)",
}
STAT_ORDER = list(STAT_UI)
STAT_META = ["free attribute points", "xp"]
HEALTH_FIELDS = ["flesh", "hit", "stun", "bandage", "wear", "rig", "hitmult"]


class Names:
    def __init__(self, db: dict[str, Any], tmap: dict[int, str]) -> None:
        self.r = db["records"]
        self.tmap = tmap
        self.unresolved: Counter = Counter()

    def name(self, sid: str) -> dict[str, Any]:
        if not sid:
            return {"sid": "", "name": "", "name_ru": ""}
        e = self.r.get(sid)
        if e is None:
            self.unresolved[sid] += 1
            return {"sid": sid, "name": None, "name_ru": None}
        return {
            "sid": sid,
            "name": e.get("name", ""),
            "name_ru": e.get("name_ru") or e.get("name", ""),
            "type": e.get("type_name") or self.tmap.get(e["type_id"], f"type_{e['type_id']}"),
            "source": e.get("defined_in") or (e["modified_in"][0] if e["modified_in"] else None),
        }


def faction_from_filename(fname: str) -> tuple[str, str]:
    stem = os.path.basename(fname)[: -len(".platoon")]
    fac, _, idx = stem.rpartition("_")
    return fac, idx


def item_row(rec: dict[str, Any], names: Names, container: str) -> dict[str, Any]:
    s, f, i = rec["string"], rec["float"], rec["int"]
    base = names.name(s.get("base data sid", ""))
    return {
        "item_sid": rec["sid"],
        "container": container,
        "section": s.get("section", ""),
        "item": base,
        "material": names.name(s.get("material sid", "")),
        "color": names.name(s.get("color sid", "")),
        "company": names.name(s.get("company sid", "")),
        "quality": f.get("quality"),
        "charges": f.get("charges"),
        "level": i.get("level"),
        "quantity": i.get("quantity"),
        "inventory_xy": [i.get("inventory x"), i.get("inventory y")],
        "item_function": i.get("item function"),
        "raw": {"bool": rec["bool"], "float": f, "int": i, "string": s},
    }


def collect_items(cont_sid: str, by: dict[str, Any], names: Names, label: str,
                  seen: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cont = by.get(cont_sid)
    if cont is None:
        return out
    for inst in cont["instances"]:
        it = by.get(inst["target"])
        if it is None or it["type"] != 42 or it["sid"] in seen:
            continue
        seen.add(it["sid"])
        out.append(item_row(it, names, label))
        # Предмет-рюкзак ссылается инстансом на свой контейнер (тип 41).
        for sub in it["instances"]:
            if sub["target"] in by and by[sub["target"]]["type"] == 41:
                out += collect_items(sub["target"], by, names, f"{label}>{it['sid']}", seen)
    return out


def character(inst: dict[str, Any], by: dict[str, Any], names: Names) -> dict[str, Any]:
    linked = {by[s]["type"]: by[s] for s in inst["states"] if s in by}
    st, stats, med, look = linked.get(36), linked.get(25), linked.get(57), linked.get(66)
    inv = linked.get(41)
    ch: dict[str, Any] = {
        "char_sid": inst["id"],
        "name": (st or {}).get("string", {}).get("name") or (stats or {}).get("name"),
        "template": names.name(inst["target"]),
        "position": inst["pos"],
        "rotation": inst["rot"],
    }
    if st:
        ch["faction"] = names.name(st["string"].get("owner faction ID", ""))
        ch["state"] = {"bool": st["bool"], "float": st["float"], "int": st["int"]}
    if look:
        race = look["refs"].get("race", [])
        ch["race"] = names.name(race[0]["sid"]) if race else None
        ch["female"] = look["bool"].get("sex female")
        ch["age"] = look["int"].get("Age")
        ch["appearance"] = {"float": look["float"], "int": look["int"], "vec3": look["vec3"],
                            "string": look["string"]}
    if stats:
        ch["stats"] = dict(stats["float"])
    if med:
        parts = []
        for n in range(16):
            if f"sid{n}" not in med["string"] and f"hit{n}" not in med["float"]:
                continue
            part = {"index": n, "part": names.name(med["string"].get(f"sid{n}", ""))}
            part.update({k: med["float"].get(f"{k}{n}") for k in HEALTH_FIELDS})
            parts.append(part)
        ch["health"] = {
            "flags": med["bool"],
            "blood": med["float"].get("blood"), "hunger": med["float"].get("hung"),
            "KO": med["float"].get("KO"), "bleeding": med["float"].get("bleeding"),
            "fed": med["float"].get("fed"), "parts": parts,
        }
    seen: set[str] = set()
    ch["items"] = collect_items(inv["sid"], by, names, "inventory", seen) if inv else []
    return ch


def nm(x: dict[str, Any] | None) -> str:
    if not x or not x.get("sid"):
        return ""
    return x.get("name_ru") or x.get("name") or ("<?>" + x.get("sid", ""))


def source_mods(source: str) -> list[str] | None:
    """Список активных модов из quick.save источника (папка или ZIP); None — если его нет."""
    for fname, _size, _enc, load in iter_entries(source):
        if fname == "quick.save":
            try:
                return parse_quick_save(load())["mods"] or None
            except (ValueError, IndexError, struct.error):
                return None
    return None


def main(zip_path: str, out_dir: str) -> None:
    act = gamedb.active_files(source_mods(zip_path))
    db = gamedb.build(act["files"])
    names = Names(db, gamedb.type_map(db))

    platoons: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    for fname, size, enc, load in iter_entries(zip_path):
        contents.append({"name": fname, "size": size, "name_encoding": enc})
        if not fname.endswith(".platoon"):
            continue
        res = read_file(load())
        assert res["consumed"] == res["size"], fname
        by = {r["sid"]: r for r in res["records"]}
        fac, idx = faction_from_filename(fname)
        for rec in res["records"]:
            if rec["type"] != 30:
                continue
            platoons.append({
                "file": os.path.basename(fname), "faction_from_filename": fac, "file_index": idx,
                "platoon_sid": rec["sid"], "char_count": rec["int"].get("char count"),
                "characters": [character(i, by, names) for i in rec["instances"]],
            })

    chars = [c for p in platoons for c in p["characters"]]
    items = [it for c in chars for it in c["items"]]
    summary = {
        "zip": os.path.basename(os.path.normpath(zip_path)),
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "files_in_zip": len(contents),
        "platoon_files": sum(1 for c in contents if c["name"].endswith(".platoon")),
        "platoons": len(platoons), "characters": len(chars), "items": len(items),
        "unresolved_sids": dict(names.unresolved.most_common()),
        "stat_ui_names": STAT_UI,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "kenshi_platoons.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "zip_contents": contents, "platoons": platoons},
                  fh, ensure_ascii=False, indent=1)

    # CSV в utf-8-sig и с ';' — открывается в русском Excel без ручного импорта.
    cfields = (["file", "faction_file", "char_sid", "name", "faction", "template", "race", "female",
                "age", "is_leader", "dead", "unconscious", "coma", "incapacitated", "blood", "hunger",
                "pos_x", "pos_y", "pos_z", "items_count"] + STAT_META + STAT_ORDER)
    with open(os.path.join(out_dir, "characters.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, cfields, delimiter=";")
        w.writeheader()
        for p in platoons:
            for c in p["characters"]:
                flags = c.get("health", {}).get("flags", {})
                row = {
                    "file": p["file"], "faction_file": p["faction_from_filename"],
                    "char_sid": c["char_sid"], "name": c["name"], "faction": nm(c.get("faction")),
                    "template": nm(c["template"]), "race": nm(c.get("race")),
                    "female": c.get("female"), "age": c.get("age"),
                    "is_leader": c.get("state", {}).get("bool", {}).get("is leader"),
                    "dead": flags.get("dead"), "unconscious": flags.get("unconcious"),
                    "coma": flags.get("coma"), "incapacitated": flags.get("incapacitated"),
                    "blood": c.get("health", {}).get("blood"), "hunger": c.get("health", {}).get("hunger"),
                    "pos_x": c["position"][0], "pos_y": c["position"][1], "pos_z": c["position"][2],
                    "items_count": len(c["items"]),
                }
                for k in STAT_META + STAT_ORDER:
                    v = c.get("stats", {}).get(k)
                    row[k] = round(v, 3) if isinstance(v, float) else v
                w.writerow(row)

    ifields = ["file", "char_sid", "char_name", "container", "section", "item_name", "item_name_en",
               "item_type", "item_base_sid", "item_source", "material", "color", "manufacturer",
               "quality", "level", "quantity", "charges", "inv_x", "inv_y", "item_sid"]
    with open(os.path.join(out_dir, "equipment.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, ifields, delimiter=";")
        w.writeheader()
        for p in platoons:
            for c in p["characters"]:
                for it in c["items"]:
                    w.writerow({
                        "file": p["file"], "char_sid": c["char_sid"], "char_name": c["name"],
                        "container": it["container"], "section": it["section"],
                        "item_name": nm(it["item"]), "item_name_en": it["item"].get("name"),
                        "item_type": it["item"].get("type"), "item_base_sid": it["item"]["sid"],
                        "item_source": it["item"].get("source"), "material": nm(it["material"]),
                        "color": nm(it["color"]), "manufacturer": nm(it["company"]),
                        "quality": it["quality"], "level": it["level"], "quantity": it["quantity"],
                        "charges": it["charges"], "inv_x": it["inventory_xy"][0],
                        "inv_y": it["inventory_xy"][1], "item_sid": it["item_sid"],
                    })
    print(json.dumps({k: v for k, v in summary.items() if k != "stat_ui_names"},
                     ensure_ascii=False, indent=1)[:3000])


def cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="platoon.zip или папка сохранения (полный экспорт)")
    ap.add_argument("out_dir", nargs="?", help="каталог для полного экспорта")
    ap.add_argument("--session", action="store_true", help="компактная выгрузка отряда игрока")
    ap.add_argument("--save", help="папка сохранения (по умолчанию — последнее сохранение)")
    ap.add_argument("--out", help="каталог сессий (по умолчанию <проект>/sessions)")
    ap.add_argument("--faction-sid", help="sid фракции игрока (по умолчанию 204-gamedata.base)")
    ap.add_argument("--explain-diff", action="store_true",
                    help="показать отслеживаемые поля сравнения и число сравнённых значений")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="сравнить две выгрузки сессии")
    args = ap.parse_args()
    if args.diff:
        import session_export
        return session_export.run_diff(args.diff[0], args.diff[1], args.explain_diff)
    if args.session:
        import session_export
        return session_export.run(args.save, args.out, args.faction_sid, explain=args.explain_diff)
    if not (args.source and args.out_dir):
        ap.error("для полного экспорта нужны source и out_dir (или --session)")
    main(args.source, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(cli())
