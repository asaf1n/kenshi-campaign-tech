"""Справочник записей игры: sid -> имя (en/ru), тип, файл-источник, поля шаблона.

Пути игры, Workshop и локализации задаются окружением или аргументами функций.
Исходные данные читаются без изменений; запись выполняется только в каталог кэша.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from collections import Counter
from typing import Any, Sequence

from tools.kenshi_gd import read_mod

PathLike = str | os.PathLike[str]

KENSHI: str = os.environ.get("KENSHI_DIR", "C:/Program Files (x86)/Steam/steamapps/common/Kenshi")
WORKSHOP: str = os.environ.get("KENSHI_WORKSHOP_DIR", "C:/Program Files (x86)/Steam/steamapps/workshop/content/233860")
PO_RU: str = f"{KENSHI}/locale/ru_RU/gamedata.po"
# Базовые файлы в порядке загрузки игры (так же они перечислены в quick.save, запись 56 refs 'mods').
BASE_FILES: dict[str, str] = {"base": "gamedata.base", "Newwworld": "Newwworld.mod", "Dialogue": "Dialogue.mod",
              "rebirth": "rebirth.mod"}
# Повышать при изменении состава полей индекса — старый кэш будет пересобран.
INDEX_VERSION: int = 8

# Поля шаблонов, нужные для характеристик предметов (itemstats.py). Остальное в индекс не попадает.
STAT_FIELDS: dict[str, tuple[str, ...]] = {
    "WEAPON": ("cut damage multiplier", "blunt damage multiplier", "weight kg", "weight mult",
               "armour penetration", "attack mod", "defence mod", "indoors mod", "skill category",
               "bleed mult", "animal damage mult", "human damage mult", "robot damage mult"),
    "WEAPON_MANUFACTURER": ("cut damage mod", "blunt damage mod", "weight mod"),
    "MATERIAL_SPECS_WEAPON": ("attack mod", "defence mod"),
    "CROSSBOW": ("pierce damage min 0", "pierce damage min 1", "pierce damage max 0", "pierce damage max 1",
                 "reload time min", "reload time min 1", "reload time max", "reload time max 1",
                 "accuracy deviation at 0 skill", "accuracy deviation at 0 skill 1", "range", "range 1",
                 "shot speed", "shot speed 1", "accuracy perfect skill", "weight kg"),
    "ARMOUR": ("class", "material type", "slot", "cut into stun", "level bonus", "blunt def bonus", "cut def bonus",
               "pierce def mult", "weight kg", "weather protection amount", "weather protection0",
               "stealth mult", "ranged skill mult", "dodge mult", "dexterity mult", "asassination mult",
               "athletics mult", "combat speed mult", "damage output mult", "fist injury mult",
               "unarmed bonus", "perception bonus", "combat attk bonus", "combat def bonus",
               "combat skill bonus", "ranged bonus"),
    "ITEM": ("item function", "artifact", "weight kg"),
    "CONTAINER": ("weight kg", "encumbrance effect"),
    "MAP_ITEM": ("weight kg",),
    "NEST_ITEM": ("weight kg",),
    "CONSTANTS": ("weapon inventory weight mult", "bow damage 1", "bow damage 99"),
    # Протезы (prosthetics.py): '<поле>' — худшее качество, '<поле> 1' — лучшее.
    "LIMB_REPLACEMENT": ("slot", "weight kg", "HP", "HP 1", "strength mult", "strength mult 1", "dexterity mult",
                         "dexterity mult 1", "athletics mult", "athletics mult 1", "swimming mult",
                         "swimming mult 1", "stealth mult", "stealth mult 1", "thievery mult", "thievery mult 1",
                         "ranged mult", "ranged mult 1", "unarmed damage bonus", "unarmed damage bonus 1",
                         "combat speed mult", "encumbrance mult", "overall mult"),
    # Исследования и постройки игрока (player_assets.py).
    "RESEARCH": ("level", "time", "money", "repeats", "blueprint only", "is level upgrade"),
    "BUILDING": ("building category",),
}
STAT_REFS: dict[str, tuple[str, ...]] = {"ARMOUR": ("part coverage",), "CROSSBOW": ("ammo",),
                                         "RESEARCH": ("enable buildings",)}
# Глобальные константы не имеют типа в gamedata.po; запись опознаётся по номеру типа.
CONSTANTS_TYPE_ID: int = 27


def find_mod(
    name: str, kenshi_dir: PathLike | None = None, workshop_dir: PathLike | None = None,
) -> str | None:
    """Файл мода по имени из списка модов сохранения/mods.cfg ('<имя>' или '<имя>.mod')."""
    kenshi = os.fspath(kenshi_dir) if kenshi_dir is not None else KENSHI
    workshop = os.fspath(workshop_dir) if workshop_dir is not None else WORKSHOP
    stem = name[:-4] if name.endswith(".mod") else name
    if stem in BASE_FILES:
        p = f"{kenshi}/data/{BASE_FILES[stem]}"
        return p if os.path.exists(p) else None
    for pattern in (f"{glob.escape(kenshi)}/mods/*/{glob.escape(stem)}.mod",
                    f"{glob.escape(workshop)}/*/{glob.escape(stem)}.mod",
                    f"{glob.escape(kenshi)}/data/{glob.escape(stem)}.mod"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def mods_cfg(kenshi_dir: PathLike | None = None) -> list[str]:
    """Активные моды из data/mods.cfg (пишет лаунчер игры) — запасной источник, если нет списка из сохранения."""
    kenshi = os.fspath(kenshi_dir) if kenshi_dir is not None else KENSHI
    try:
        with open(f"{kenshi}/data/mods.cfg", encoding="utf-8-sig") as fh:
            return [line.strip() for line in fh if line.strip()]
    except OSError:
        return []


def active_files(
    mod_names: list[str] | None, kenshi_dir: PathLike | None = None,
    workshop_dir: PathLike | None = None,
) -> dict[str, Any]:
    """Файлы данных в порядке загрузки игры.

    mod_names — список из quick.save (запись 56) или None; тогда базовые файлы + data/mods.cfg.
    """
    source = "quick.save"
    if not mod_names:
        source = "mods.cfg"
        mod_names = list(BASE_FILES) + mods_cfg(kenshi_dir)
    files: list[str] = []
    missing: list[str] = []
    for n in mod_names:
        p = find_mod(n, kenshi_dir, workshop_dir)
        if p is None:
            missing.append(n)
        elif p not in files:
            files.append(p)
    return {"files": files, "missing": missing, "source": source, "mods": mod_names}


def game_files(
    kenshi_dir: PathLike | None = None, workshop_dir: PathLike | None = None,
) -> list[str]:
    """Все установленные данные (без контекста сохранения): базовые файлы в порядке игры + все моды."""
    kenshi = os.fspath(kenshi_dir) if kenshi_dir is not None else KENSHI
    workshop = os.fspath(workshop_dir) if workshop_dir is not None else WORKSHOP
    base = [f"{kenshi}/data/{n}" for n in BASE_FILES.values()]
    mods = sorted(glob.glob(f"{glob.escape(kenshi)}/mods/*/*.mod")
                  + glob.glob(f"{glob.escape(workshop)}/*/*.mod"))
    return [f for f in base if os.path.exists(f)] + mods


def _po_string(block: str, key: str) -> str | None:
    m = re.search(rf'^{key} ((?:".*"\n?)+)', block, re.M)
    if not m:
        return None
    return "".join(re.findall(r'"(.*)"', m.group(1))).replace('\\"', '"')


def load_po(path: PathLike | None = None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Возвращает (sid -> перевод, sid -> имя типа, msgid -> msgstr) из gamedata.po.

    В файле нет msgctxt, поэтому игра переводит строку по английскому тексту (msgid):
    имя записи переводится, если такой msgid есть у любой записи. sid -> перевод оставлен
    для сравнения; имя типа берётся из комментария '#. Type:' у ссылки '#: <sid>' без ':N'.
    """
    ru: dict[str, str] = {}
    types: dict[str, str] = {}
    by_msgid: dict[str, str] = {}
    try:
        with open(path if path is not None else PO_RU, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return ru, types, by_msgid
    for block in text.split("\n\n"):
        msgid, msgstr = _po_string(block, "msgid"), _po_string(block, "msgstr")
        if msgid and msgstr:
            by_msgid[msgid] = msgstr
        m_ref = re.search(r"^#: (\S+)$", block, re.M)
        if not m_ref or ":" in m_ref.group(1):
            continue
        sid = m_ref.group(1)
        m_type = re.search(r"^#\. Type: (.+)$", block, re.M)
        if m_type:
            types[sid] = m_type.group(1).strip()
        if msgstr:
            ru[sid] = msgstr
    return ru, types, by_msgid


def build(files: Sequence[PathLike] | None = None, po_path: PathLike | None = None) -> dict[str, Any]:
    """Сводит записи файлов данных в порядке загрузки: поле более позднего файла заменяет раннее."""
    names: dict[str, dict[str, Any]] = {}
    for f in files if files is not None else game_files():
        src = os.path.basename(f)
        with open(f, "rb") as fh:
            records = read_mod(fh.read())["records"]
        for rec in records:
            e = names.setdefault(rec["sid"], {"type_id": rec["type"], "defined_in": None, "modified_in": []})
            if rec["name"]:
                e["name"] = rec["name"]
            if rec["sid"].endswith("-" + src):
                e["defined_in"] = src
            else:
                e["modified_in"].append(src)
            e["type_id"] = rec["type"]
            e.setdefault("data", {})
            for sec in ("float", "int", "bool", "string"):
                e["data"].setdefault(sec, {}).update(rec[sec])
            refs = e.setdefault("refs", {})
            for cat, lst in rec["refs"].items():
                cur = refs.setdefault(cat, {})
                for x in lst:
                    cur[x["sid"]] = x["v"]
    ru, types, by_msgid = load_po(po_path)
    for sid, e in names.items():
        tr = by_msgid.get(e.get("name", ""))
        if tr:
            e["name_ru"] = tr
        if sid in types:
            e["type_name"] = types[sid]
    return {"records": names, "po_ru": ru, "po_types": types}


def type_map(db: dict[str, Any]) -> dict[int, str]:
    """Числовой тип записи -> имя типа: берётся из комментариев "#. Type:" в gamedata.po."""
    tm: dict[int, Counter[str]] = {}
    for e in db["records"].values():
        if "type_name" in e:
            tm.setdefault(e["type_id"], Counter())[e["type_name"]] += 1
    out = {k: v.most_common(1)[0][0] for k, v in tm.items()}
    out.setdefault(CONSTANTS_TYPE_ID, "CONSTANTS")
    return out


def fingerprint(files: Sequence[PathLike], po_path: PathLike | None = None) -> list[list[Any]]:
    """Состав, порядок и версии файлов данных: любое изменение модов или локализации меняет отпечаток."""
    out: list[list[Any]] = [["index_version", INDEX_VERSION]]
    for f in files:
        st = os.stat(f)
        out.append([os.fspath(f), st.st_size, st.st_mtime_ns])
    po = os.fspath(po_path) if po_path is not None else PO_RU
    try:
        st = os.stat(po)
    except FileNotFoundError:
        # Появление локализации должно инвалидировать кэш без перевода.
        out.append([po, None, None])
    else:
        out.append([po, st.st_size, st.st_mtime_ns])
    return out


def _stats(e: dict[str, Any], tname: str) -> dict[str, Any] | None:
    keys = STAT_FIELDS.get(tname)
    if not keys:
        return None
    d = e.get("data", {})
    # string — первым: при совпадении имён числовое поле важнее.
    out = {k: d[s][k] for k in keys for s in ("string", "float", "int", "bool") if k in d.get(s, {})}
    for cat in STAT_REFS.get(tname, ()):
        refs = e.get("refs", {}).get(cat)
        if refs:
            out["refs:" + cat] = refs
    return out


def load_index(
    cache_dir: PathLike, files: Sequence[PathLike] | None = None, po_path: PathLike | None = None,
) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    """Компактный индекс sid -> [name_en, name_ru, type, source, stats|None] с кэшем на диске.

    Строится тем же build(), что и полный экспорт. Кэш отдельный для каждого набора файлов
    (у разных сохранений могут быть разные моды) и пересобирается при изменении отпечатка.
    """
    files = files if files is not None else game_files()
    fp = fingerprint(files, po_path)
    key = hashlib.sha1(json.dumps(fp).encode("utf-8")).hexdigest()[:16]
    cache_path = os.path.join(cache_dir, f"gamedb_{key}.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        if isinstance(cached, dict) and cached.get("fingerprint") == fp:
            return cached["index"], {"cache": "hit", "files": len(files), "cache_file": os.path.basename(cache_path)}
    except (OSError, ValueError, KeyError):
        pass
    db = build(files, po_path)
    tmap = type_map(db)
    index: dict[str, list[Any]] = {}
    for sid, e in db["records"].items():
        tname = e.get("type_name") or tmap.get(e["type_id"], f"type_{e['type_id']}")
        index[sid] = [
            e.get("name", ""),
            e.get("name_ru") or e.get("name", ""),
            tname,
            e.get("defined_in") or (e["modified_in"][0] if e["modified_in"] else None),
            _stats(e, tname),
        ]
    os.makedirs(cache_dir, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": fp, "index": index}, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, cache_path)
    return index, {"cache": "rebuilt", "files": len(files), "cache_file": os.path.basename(cache_path)}
