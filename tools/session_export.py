"""Режим --session: компактный JSON отряда игрока из последнего сохранения + сравнение с прошлой выгрузкой.

Формат результата — README.md, раздел «Режим сессии»; статусы проверки полей — docs/item-stats.md.
"""
import datetime
import glob
import json
import os
import struct
import sys
import time
from collections import Counter, defaultdict
from typing import Any

import gamedb
import itemstats
import player_assets
import prosthetics
import saves
from tools import diff as session_diff
from export_platoons import STAT_UI, character, item_row
from kenshi_gd import read_file

FORMAT = "kenshi-session/10"
# Форматы с той же структурой персонажей; сравнение между ними допустимо (v3 изменил только смысл id,
# v6 добавил research и property, v7 — prosthetics, lost_limbs и belt, v8 — carried_weight_kg,
# v9 — grade и hp_max протезов, v10 — nutrition еды).
COMPATIBLE_FORMATS = {"kenshi-session/2", "kenshi-session/3", "kenshi-session/4", "kenshi-session/5",
                      "kenshi-session/6", "kenshi-session/7", "kenshi-session/8", "kenshi-session/9", FORMAT}
# changes.history: смены экипировки, протезов и состава между более ранними выгрузками за столько игровых дней.
# Выгрузок бывает несколько между передачами файла в чат-ИИ модели, а changes сравнивает только с последней.
HISTORY_DAYS = 7
HISTORY_MAX_SNAPSHOTS = 40  # ограничение времени чтения sessions/
# Итоговый файл для передачи в чат-ИИ модели: последний снимок + изменения; кладётся рядом с kenshi-session.bat.
LATEST_FILE = "kenshi-session.json"
# Фракция игрока Kenshi (Nameless). Принадлежность персонажа берётся из его записи
# состояния ('owner faction ID'), имя файла отряда не используется.
DEFAULT_PLAYER_FACTION = "204-gamedata.base"
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT, "cache")

ATTRIBUTES = ["strength", "dexterity", "toughness2", "perception"]
# Ключи навыков, названия которых подтверждены fcs.def (раздел [STATS]); остальные — скрытые поля.
STATS_IN_FCS = {"strength", "dexterity", "toughness2", "perception", "attack", "defence", "dodge", "unarmed",
                "athletics", "swimming", "engineer", "medic", "robotics", "science", "assassin", "lockpicking",
                "stealth", "thievery", "armour smith", "bow smith", "cooking", "farming", "labouring",
                "weapon smith", "blunt", "bow", "ff", "hackers", "heavy weapons", "katana", "poles", "sabres",
                "turrets"}
SKILL_NAMES = {k: ("Precision Shooting" if k == "ff" else v) for k, v in STAT_UI.items() if k in STATS_IN_FCS}
WEAPON_SLOTS = {"back": "weapon_back", "hip": "weapon_hip"}
ARMOUR_SLOTS = {"head": "head", "shirt": "shirt", "armour": "body", "legs": "legs", "boots": "boots"}
BACKPACK_SECTION = "backpack_attach"
BELT_SECTION = "belt"
INVENTORY_SECTION = "main"
HEALTH_KEYS = ["flesh", "bandage", "wear", "stun", "hit"]

FIELD_NOTES = {
    "id": "Стабильный идентификатор персонажа: 'ps:' + portrait_serial из сохранения; не меняется между "
          "сохранениями и при переводе в другой отряд.",
    "squad_id/squad_name": "Файл отряда и имя отряда из реестра quick.save (запись 34).",
    "is_animal": "Шаблон персонажа имеет тип ANIMAL_CHARACTER.",
    "attributes_base/skills_base": "Базовые значения из сохранения. Интерфейс показывает эффективное значение и "
                                   "модификатор: «Сила 34 (+1)» при base 33.35. Эффективные значения не вычисляются.",
    "hidden_stats_raw": "Поля записи навыков, которых нет в разделе [STATS] fcs.def; в интерфейсе не показываются.",
    "weapons/armour": "Слот: weapon_back = «Оружие I», weapon_hip = «Оружие II», head, shirt, body ('armour'), "
                      "legs, boots. Пустой слот брони — null. template_unshown — поля шаблона, которых нет в "
                      "подсказке игры. Статусы полей — meta.verification.",
    "level": "Поле 'level' экземпляра из сохранения. Для оружия соответствует модели (grade), для брони и "
             "арбалетов грейд выводится только для уровней, сверенных с интерфейсом.",
    "backpack/inventory": "Сводка: одна строка на базовый предмет, quantity суммируется; type не указан "
                          "у обычных предметов (ITEM); stolen — сколько из них краденых.",
    "stolen": "Предмет помечен в игре «Украдено» (владелец — другой персонаж).",
    "armour.resistances": "cut_pct/blunt_pct — сопротивление в процентах, harpoon — единицы; считаются по формулам "
                          "движка (Kenshi Wiki «Armor Stats»), интерфейс показывает целую часть.",
    "armour.skill_modifiers": "Итоговые множители как в подсказке игры: stealth/athletics/combat_speed рассчитаны "
                              "с поправкой движка по классу, материалу и уровню.",
    "belt": "Предмет на поясе (слот 'belt', например фонарь); null — пусто.",
    "prosthetics": "Установленные протезы: limb — конечность (left_arm, right_arm, left_leg, right_leg), part — "
                   "часть тела в health, level — уровень предмета, grade — грейд как в подсказке (только для "
                   "сверенных уровней 80 и 95, иначе null), hp_max — «ОЗ Макс». template_range — [значение при "
                   "худшем, при лучшем качестве] из шаблона; бонусы и штрафы при уровне предмета не вычисляются "
                   "(расчёт расходится с подсказкой у Силы и Ловкости). Текущее состояние протеза — "
                   "health[part]: flesh, wear (износ).",
    "lost_limbs": "Потерянные конечности без протеза; в health у такой части limb = 'lost'.",
    "carried_weight_kg": "«Общий вес» в инвентаре (интерфейс показывает целую часть): экипировка, пояс, рюкзак и "
                         "инвентарь полностью, содержимое надетого рюкзака — с его коэффициентом; протезы не "
                         "учитываются. null — у предмета неизвестен вес (см. warnings); у животных поля нет. "
                         "Грузоподъёмность в игре = 15 + эффективная Сила (с учётом травм, протезов и снаряжения), "
                         "её в сохранении нет; базовая Сила из attributes_base для неё не подходит, процент перегруза "
                         "не выводится.",
    "squads.members": "Имена участников; связь с персонажем — characters[].squad_id. Пустой отряд из реестра "
                      "quick.save выводится с пустым members.",
    "changes": "Только в kenshi-session.json: сравнение с предыдущей выгрузкой (тот же объект, что "
               "sessions/session_diff.json).",
    "changes.history": f"Смены экипировки, протезов и состава (пришли, ушли, имя, отряд) между более ранними "
                       f"выгрузками за {HISTORY_DAYS} игровых дней — до changes.from; шаги без изменений не "
                       "перечислены, но учтены в pairs и compared.",
    "supplies": "Категории по полю 'item function' шаблона предмета; состав категорий — docs/item-stats.md.",
    "nutrition": "Еда и сырая еда: суммарная питательность («пит» в подсказке) — остаток 'charges' каждого "
                 "экземпляра × quantity; у надкушенного предмета меньше, чем у целого. С интерфейсом не сверено "
                 "(meta.verification['item.nutrition']). null — у части предметов значения нет. Темп голода не "
                 "выводится; «Голод» в интерфейсе = condition.hunger_raw × 100.",
    "health": "Поля частей тела как в сохранении. Интерфейс показывает целую часть flesh − stun (при stun 0 — "
              "flesh). bandage — перевязанный урон (жёлтый участок полосы), wear — износ механических частей и "
              "протезов. Максимум части числом в интерфейсе не показан; hit в интерфейсе не отображается. "
              "limb: 'prosthetic' — на месте части протез (см. prosthetics), 'lost' — конечность потеряна.",
    "condition": "blood — «Кровь» (у скелетов «Масло»); hunger_raw × 100 — «Голод» в интерфейсе; "
                 "KO, bleeding — сырые значения.",
    "world": "quick.save, запись 56: день, время, деньги — совпадают с интерфейсом.",
    "research": "quick.save, запись 21: completed — завершённые (finished#); in_progress — первая запись очереди "
                "(current0), queue — остальные (current1…); paid — оплачено на научном стенде (paid#), у "
                "исследований по чертежу (blueprint_only) — false; список research.paid — оплаченные вне очереди и "
                "не завершённые (обычно пуст). progress_raw — 'current prog#' как есть. "
                "level, blueprint_only, level_upgrade, unlocks ('enable buildings') — поля шаблона RESEARCH. "
                "Доступные к изучению исследования не вычисляются.",
    "research.tech_level": "В сохранении нет поля тех. уровня; tech_level = null. level_upgrades_completed — "
                           "завершённые исследования с 'is level upgrade' (названия как в игре).",
    "property.buildings": "Здания с 'owner faction ID' = фракция игрока из всех zone/*.zone. Верхний уровень — "
                          "здания вне других зданий игрока; interior — объекты внутри (станки, хранилища, койки, "
                          "свет), floor — этаж. id — инстанс здания (стабилен между сохранениями); name — имя в "
                          "игре, template — шаблон; category — 'building category' шаблона; town — город по "
                          "ссылке здания на запись 94 quick.save (вне городов — null); position — как у персонажей.",
    "property.status": "complete — 'is complete'; destroyed — 'destroyed'; construction_progress_raw — "
                       "'construction progress' как есть (у готовых зданий — не доля, а число > 1).",
    "property.storage": "Сводка содержимого контейнеров здания, как inventory персонажа; пометка stolen не выводится.",
    "property.player_towns": "Города, где quick.save (запись 94, 'factions here') числит фракцию игрока; "
                             "город без найденных зданий — предупреждение.",
    "not_exported": "Грузоподъёмность и процент перегруза (нужна эффективная Сила), роль персонажа — в сохранении нет. Доступные исследования, "
                    "тех. уровень числом, права владения (куплено/построено) — не выводятся.",
}


# Статусы полей персонажа и мира; контрольные значения — tests/fixtures/ui_*.json, сверка — tools/verify_ui.py.
CHAR_VERIFICATION = {
    "stats.base": {"status": "verified", "ui_samples": 47,
                   "how": "целая часть base = значение интерфейса − модификатор",
                   "note": "44 точно; 3 расходятся на 1 у Уворота с множительными модификаторами"},
    "health.flesh": {"status": "verified", "ui_samples": 84,
                     "how": "целая часть flesh − stun = значение части тела в интерфейсе",
                     "note": "у неподвижного скелета 7 из 7 точно; у заживающих расхождение ≤ 1 из-за времени снимка; "
                             "вычитание stun — день 75: 7 частей со stun > 0 совпали только с flesh − stun"},
    "condition.blood": {"status": "verified", "ui_samples": 7, "how": "«Кровь» / «Масло»"},
    "condition.hunger_raw": {"status": "verified", "ui_samples": 5, "how": "× 100 = «Голод»"},
    "health.bandage/wear/max": {"status": "partially_verified",
                                "how": "bandage совпадает с жёлтым участком полосы; максимум числом не показан"},
    "world": {"status": "verified", "ui_samples": 1, "how": "день 44, 12:32, к.2 685 — как в интерфейсе"},
    "squad_name": {"status": "verified", "ui_samples": 3, "how": "названия вкладок отрядов"},
    "id": {"status": "verified", "how": "portrait_serial одинаков в 3 сохранениях и после перевода в другой отряд, "
                                        "уникален у 14 персонажей"},
}


class IndexNames:
    """Разрешение sid через компактный индекс; интерфейс name() совпадает с export_platoons.Names."""

    def __init__(self, index: dict[str, list[Any]]) -> None:
        self.ix = index
        self.unresolved: Counter = Counter()

    def name(self, sid: str) -> dict[str, Any]:
        if not sid:
            return {"sid": "", "name": "", "name_ru": ""}
        e = self.ix.get(sid)
        if e is None:
            self.unresolved[sid] += 1
            return {"sid": sid, "name": None, "name_ru": None}
        return {"sid": sid, "name": e[0], "name_ru": e[1] or e[0], "type": e[2], "source": e[3]}

    def stats(self, sid: str) -> dict[str, Any]:
        e = self.ix.get(sid)
        return (e[4] or {}) if e and len(e) > 4 else {}

    def type_of(self, sid: str) -> str | None:
        e = self.ix.get(sid)
        return e[2] if e else None

    def constants(self) -> dict[str, Any]:
        for e in self.ix.values():
            if e[2] == "CONSTANTS" and e[4]:
                return e[4]
        return {}


class Timer:
    def __init__(self) -> None:
        self.ms: dict[str, float] = defaultdict(float)

    def add(self, key: str, t0: float) -> float:
        now = time.perf_counter()
        self.ms[key] += (now - t0) * 1000
        return now


def _r(v: Any, nd: int = 2) -> Any:
    return round(v, nd) if isinstance(v, float) else v


def _drop_empty(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def char_key(state_int: dict[str, int]) -> str | None:
    """Стабильный id персонажа.

    'portrait_serial' не меняется между сохранениями и при переводе в другой отряд (проверено);
    handle* описывает отряд и номер в нём и при переводе меняется — только запасной вариант.
    """
    if state_int.get("portrait_serial"):
        return f"ps:{state_int['portrait_serial']}"
    parts = [state_int.get(k) for k in ("handleTYPE", "handleC", "handleS", "handleI", "handleCS")]
    if any(p is None for p in parts):
        return None
    return "h:" + ":".join(str(p) for p in parts)


def _label(n: dict[str, Any]) -> str | None:
    return (n.get("name_ru") or n.get("name") or None) if n.get("sid") else None


def item_head(it: dict[str, Any]) -> dict[str, Any]:
    """Название (ru как в игре), английское — если отличается, sid; неразрешённый sid помечается."""
    item = it["item"]
    head = _drop_empty({"name": item.get("name_ru"), "sid": item["sid"],
                        "name_en": item.get("name") if item.get("name") != item.get("name_ru") else None})
    return (head | ({"unresolved": True} if item.get("name") is None and item["sid"] else {})
            | ({"stolen": True} if is_stolen(it) else {}))


def is_stolen(it: dict[str, Any]) -> bool:
    """«Украдено» в интерфейсе: владелец предмета — другой персонаж (ownedbyTYPE = 1)."""
    return it.get("raw", {}).get("int", {}).get("ownedbyTYPE") == 1


def weapon_entry(it: dict[str, Any], names: IndexNames, const: dict[str, Any]) -> dict[str, Any]:
    item, level = it["item"], it["level"]
    out = {"slot": WEAPON_SLOTS[it["section"]]} | item_head(it)
    tname = item.get("type")
    t = names.stats(item["sid"])
    if tname == "CROSSBOW":
        out |= {"type": "crossbow", "level": level, "grade": itemstats.grade_for("CROSSBOW", level)}
        out |= itemstats.crossbow_stats(t, level, const) if level is not None and t else {}
        ammo = t.get("refs:ammo") or {}
        if ammo:
            asid = next(iter(ammo))
            out["ammo"] = _label(names.name(asid)) or asid
            out["ammo_sid"] = asid
    elif tname == "WEAPON":
        model, manuf = it["material"], it["company"]
        out |= {"type": "melee", "level": level, "grade": _label(model),
                "manufacturer": _label(manuf)}
        if t:
            out |= itemstats.weapon_view(t, names.stats(manuf["sid"]) if manuf.get("sid") else None, level, const)
    else:
        out |= {"type": tname, "level": level}
    # grade остаётся и при null: «грейд не подтверждён» — тоже сведение.
    keep_grade = {"grade": out.get("grade")} if tname in ("CROSSBOW", "WEAPON") else {}
    return _drop_empty(out) | keep_grade


def armour_entry(it: dict[str, Any], names: IndexNames) -> dict[str, Any]:
    level = it["level"]
    out = {"slot": ARMOUR_SLOTS[it["section"]]} | item_head(it) | {"level": level}
    t = names.stats(it["item"]["sid"])
    if t:
        out |= itemstats.armour_view(t, level, lambda sid: _label(names.name(sid)) or sid,
                                     lambda sid: names.name(sid).get("name") == "Chest")
    return out


def aggregate(items: list[dict[str, Any]], names: IndexNames) -> list[dict[str, Any]]:
    """Сводка предметов: одна строка на базовый предмет, количество суммируется."""
    agg: dict[str, dict[str, Any]] = {}
    for it in items:
        sid = it["item"]["sid"]
        cat = itemstats.SUPPLY_CATEGORIES.get(names.stats(sid).get("item function"))
        tname = it["item"].get("type")
        a = agg.setdefault(sid, _drop_empty({"name": it["item"].get("name_ru"), "sid": sid,
                                             "type": tname if tname != "ITEM" else None, "category": cat})
                           | {"quantity": 0}
                           | ({"unresolved": True} if it["item"].get("name") is None else {}))
        a["quantity"] += it["quantity"] or 0
        if cat in itemstats.NUTRITION_CATEGORIES:
            # 'charges' — на единицу стопки; если у части экземпляров его нет — None, без частичной суммы.
            ch, acc = it.get("charges"), a.get("nutrition", 0.0)
            a["nutrition"] = None if ch is None or acc is None else round(acc + ch * (it["quantity"] or 0), 1)
        if is_stolen(it):
            a["stolen"] = a.get("stolen", 0) + (it["quantity"] or 0)
    return sorted(agg.values(), key=lambda a: (a.get("category") or "~", a.get("type") or "", a.get("name") or a["sid"]))


def prosthetic_entry(limb: str, rec: dict[str, Any], part: str | None, names: IndexNames) -> dict[str, Any]:
    it = item_row(rec, names, "limb")
    level = it["level"]
    t = names.stats(it["item"]["sid"])
    # grade остаётся и при null: «грейд не подтверждён» — тоже сведение.
    out = ({"limb": limb, "part": part} | item_head(it)
           | {"level": level, "grade": prosthetics.CONFIRMED_GRADES.get(level) if level is not None else None})
    if t and prosthetics.hp_max(t, level) is not None:
        out["hp_max"] = prosthetics.hp_max(t, level)
    return out | (prosthetics.template_view(t) if t else {})


def carried_weight(items: list[dict[str, Any]], names: IndexNames,
                   const: dict[str, Any]) -> tuple[float | None, list[str]]:
    """«Общий вес» персонажа и предметы с неизвестным весом (тогда вес — None)."""
    by_sid = {it["item_sid"]: it for it in items}
    rows, unknown = [], []
    for it in items:
        mult = 1.0
        # container = 'inventory>sid1>sid2': коэффициент дают надетые рюкзаки среди предков.
        for parent in (by_sid.get(s) for s in it["container"].split(">")[1:]):
            if parent and parent["section"] == BACKPACK_SECTION:
                eff = names.stats(parent["item"]["sid"]).get("encumbrance effect")
                mult = mult * eff if eff is not None else float("nan")
        manuf = names.stats(it["company"]["sid"]) if it["company"].get("sid") else None
        w = itemstats.item_weight(it["item"].get("type"), names.stats(it["item"]["sid"]), it["level"], manuf,
                                  const, lambda sid: names.name(sid).get("name") == "Chest")
        if w is None or mult != mult:
            unknown.append(it["item"].get("name") or it["item"]["sid"])
        rows.append((w, it["quantity"] if it["quantity"] is not None else 1, mult))
    total = None if unknown else itemstats.carried_weight(rows)
    return (None if total is None else round(total, 2)), unknown


def limb_parts(health_parts: list[dict[str, Any]]) -> dict[str, int]:
    """Конечность -> номер части в списке здоровья (по английскому имени части тела)."""
    by_en = {(p["part"].get("name") or "").strip().lower(): n for n, p in enumerate(health_parts)}
    return {limb: by_en[en] for limb, en in prosthetics.LIMB_PART_EN.items() if en in by_en}


def compact_character(ch: dict[str, Any], squad: dict[str, Any], names: IndexNames,
                      const: dict[str, Any], warnings: list[str],
                      limbs: tuple[dict[str, dict[str, Any]], list[str]] = ({}, [])) -> dict[str, Any]:
    """limbs — результат prosthetics.read_limbs: протезы по конечностям и потерянные конечности."""
    st_int = ch.get("state", {}).get("int", {})
    key = char_key(st_int)
    if key is None:
        key = f"name:{ch['name']}"
        warnings.append(f"У персонажа '{ch['name']}' нет portrait_serial и handle; ключ для сравнения — имя")
    stats = ch.get("stats", {})

    weapons, armour, other = [], {s: None for s in ARMOUR_SLOTS.values()}, []
    backpack, backpack_items, inventory, belt = None, [], [], None
    for it in ch["items"]:
        sec = it["section"]
        if it["container"] != "inventory":
            backpack_items.append(it)
        elif sec == INVENTORY_SECTION:
            inventory.append(it)
        elif sec in WEAPON_SLOTS:
            weapons.append(weapon_entry(it, names, const))
        elif sec in ARMOUR_SLOTS:
            slot = ARMOUR_SLOTS[sec]
            entry = armour_entry(it, names)
            if armour[slot] is None:
                armour[slot] = entry
            else:
                other.append(entry)
                warnings.append(f"'{ch['name']}': второй предмет в слоте {slot} — в other_slots")
        elif sec == BACKPACK_SECTION and backpack is None:
            backpack = item_head(it)
        elif sec == BELT_SECTION and belt is None:
            belt = item_head(it)
        else:
            other.append({"section": sec} | item_head(it))
            warnings.append(f"'{ch['name']}': предмет в неизвестном слоте '{sec}' — в other_slots")
    if backpack is not None:
        backpack["contents"] = aggregate(backpack_items, names)
    elif backpack_items:
        warnings.append(f"'{ch['name']}': содержимое контейнера без рюкзака в слоте")
        backpack = {"contents": aggregate(backpack_items, names)}
    inv = aggregate(inventory, names)
    supplies: Counter = Counter()
    for row in inv + (backpack or {}).get("contents", []):
        if row.get("category"):
            supplies[row["category"]] += row["quantity"]

    health = ch.get("health", {})
    flags = health.get("flags", {})
    raw_parts = health.get("parts", [])
    parts = [{"part": p["part"].get("name_ru") or p["part"]["sid"]} | {k: _r(p.get(k)) for k in HEALTH_KEYS}
             for p in raw_parts]
    installed, lost = limbs
    where = limb_parts(raw_parts)
    for limb, mark in [(x, "prosthetic") for x in installed] + [(x, "lost") for x in lost]:
        if limb in where:
            parts[where[limb]] = {"part": parts[where[limb]]["part"], "limb": mark} | parts[where[limb]]
        else:
            warnings.append(f"'{ch['name']}': часть тела для {limb} не найдена в записи здоровья")
    prosth = [prosthetic_entry(limb, rec, parts[where[limb]]["part"] if limb in where else None, names)
              for limb, rec in sorted(installed.items(), key=lambda x: prosthetics.LIMBS.index(x[0]))]
    template = ch["template"]
    is_animal = names.type_of(template["sid"]) == "ANIMAL_CHARACTER"
    weight: dict[str, Any] = {}
    if not is_animal:
        w, unknown = carried_weight(ch["items"], names, const)
        weight = {"carried_weight_kg": w}
        if unknown:
            warnings.append(f"'{ch['name']}': вес не посчитан — неизвестен вес: {', '.join(sorted(set(unknown)))}")
    out = {
        "id": key,
        "name": ch["name"],
        "race": _label(ch.get("race") or {}),
        "is_animal": is_animal,
        "female": ch.get("female"),
        "squad_id": squad["squad_id"],
        "squad_name": squad.get("squad_name"),
        "is_leader": ch.get("state", {}).get("bool", {}).get("is leader"),
        "position": [round(v, 1) for v in ch["position"]],
        "status": {"dead": flags.get("dead"), "unconscious": flags.get("unconcious"),
                   "coma": flags.get("coma"), "incapacitated": flags.get("incapacitated")},
        "condition": {"blood": _r(health.get("blood")), "hunger_raw": _r(health.get("hunger"), 4),
                      "KO": _r(health.get("KO")), "bleeding": _r(health.get("bleeding"))},
        "attributes_base": {SKILL_NAMES[k]: _r(stats[k]) for k in ATTRIBUTES if k in stats},
        "skills_base": {SKILL_NAMES[k]: _r(stats[k]) for k in STAT_UI
                        if k in stats and k in SKILL_NAMES and k not in ATTRIBUTES},
        "hidden_stats_raw": {k: _r(v) for k, v in stats.items() if k not in SKILL_NAMES},
        "weapons": weapons,
        "armour": armour,
        "belt": belt,
        "prosthetics": prosth,
        "lost_limbs": lost,
        "backpack": backpack,
        "inventory": inv,
        "supplies": dict(sorted(supplies.items())),
        **weight,
        "health": parts,
        "other_slots": other,
    }
    return {k: v for k, v in out.items() if k not in ("other_slots", "hidden_stats_raw", "lost_limbs") or v}


def party_supplies(chars: list[dict[str, Any]], names: IndexNames) -> dict[str, Any]:
    """Запасы отряда по категориям: итог и по предметам с владельцами."""
    cats: dict[str, dict[str, Any]] = {}
    artifacts = []
    for c in chars:
        rows = c["inventory"] + (c.get("backpack") or {}).get("contents", [])
        for row in rows:
            if names.stats(row["sid"]).get("artifact"):
                artifacts.append({"name": row.get("name"), "sid": row["sid"], "holder": c["name"]})
            cat = row.get("category")
            if not cat:
                continue
            bucket = cats.setdefault(cat, {"total": 0, "items": {}})
            bucket["total"] += row["quantity"]
            item = bucket["items"].setdefault(row["sid"], {"name": row.get("name"), "sid": row["sid"],
                                                           "quantity": 0, "holders": {}})
            item["quantity"] += row["quantity"]
            item["holders"][c["name"]] = item["holders"].get(c["name"], 0) + row["quantity"]
            if "nutrition" in row:
                for acc in (bucket, item):
                    n = acc.get("nutrition", 0.0)
                    acc["nutrition"] = None if n is None or row["nutrition"] is None else round(n + row["nutrition"], 1)
    out: dict[str, Any] = {cat: {"total": b["total"]} | ({"nutrition": b["nutrition"]} if "nutrition" in b else {})
                           | {"items": sorted(b["items"].values(), key=lambda x: x["name"] or "")}
                           for cat, b in sorted(cats.items())}
    if artifacts:
        out["artifacts"] = artifacts
    return out


def collect(save_dir: str, faction_sid: str, names: IndexNames, timer: Timer | None = None,
            qs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Читает отряды игрока из папки сохранения. Без побочных эффектов."""
    timer = timer or Timer()
    warnings: list[str] = []
    counts: dict[str, Any] = {}
    registry: dict[str, dict[str, Any]] | None = None
    if qs is None:
        try:
            qs = saves.read_quick_save(save_dir)
        except Exception as e:  # noqa: BLE001 — quick.save необязателен, выгрузка продолжается без него
            warnings.append(f"quick.save не прочитан ({e!r}); используются все файлы platoon/, "
                            "возможны устаревшие отряды; мир и список модов недоступны")
    if qs is not None:
        registry, counts = qs["registry"], qs["player_counts"]
    const = names.constants()
    pdir = os.path.join(save_dir, "platoon")
    files = sorted(f for f in os.listdir(pdir) if f.endswith(".platoon"))
    used = [f for f in files if registry is None or f[:-8] in registry]
    needle = faction_sid.encode("utf-8")

    chars: list[dict[str, Any]] = []
    squads: list[dict[str, Any]] = []
    parsed, excluded = [], 0
    for f in used:
        t0 = time.perf_counter()
        with open(os.path.join(pdir, f), "rb") as fh:
            data = fh.read()
        t0 = timer.add("read", t0)
        # sid фракции хранится в файле строкой: без неё в файле не может быть персонажей игрока.
        if needle not in data:
            continue
        res = read_file(data)
        if res["consumed"] != res["size"]:
            raise ValueError(f"{f}: прочитано {res['consumed']} из {res['size']} байт")
        t0 = timer.add("parse", t0)
        parsed.append(f)
        by = {r["sid"]: r for r in res["records"]}
        stem = f[:-8]
        reg = (registry or {}).get(stem, {})
        squad = {"squad_id": stem, "squad_name": reg.get("name")}
        members = []
        for rec in res["records"]:
            if rec["type"] != 30:
                continue
            for inst in rec["instances"]:
                ch = character(inst, by, names)
                if ch.get("faction", {}).get("sid") != faction_sid:
                    excluded += 1
                    continue
                med = next((by[s] for s in inst["states"] if s in by and by[s]["type"] == 57), None)
                limbs = prosthetics.read_limbs(med, by, warnings, ch["name"])
                c = compact_character(ch, squad, names, const, warnings, limbs)
                chars.append(c)
                members.append(c["name"])
        timer.add("resolve", t0)
        if members:
            squads.append(_drop_empty(squad | {"squad_index": reg.get("squad_index"), "dead": reg.get("dead")})
                          | {"members": members})
    # Пустой отряд игрока есть в реестре и в счётчике 'squads', но его файл не содержит sid фракции.
    found = {s["squad_id"] for s in squads}
    for stem, reg in sorted((registry or {}).items(), key=lambda x: (x[1].get("squad_index") is None,
                                                                      x[1].get("squad_index"), x[0])):
        if reg.get("faction_sid") == faction_sid and stem not in found and stem + ".platoon" in used:
            squads.append(_drop_empty({"squad_id": stem, "squad_name": reg.get("name"),
                                       "squad_index": reg.get("squad_index"), "dead": reg.get("dead")})
                          | {"members": []})
    # Сверка с независимыми счётчиками quick.save; расхождение — только предупреждение.
    if counts.get("members") is not None and counts["members"] != len(chars):
        warnings.append(f"quick.save: members={counts['members']}, а в .platoon найдено {len(chars)}")
    if counts.get("squads") is not None and counts["squads"] != len(squads):
        warnings.append(f"quick.save: squads={counts['squads']}, а в .platoon найдено {len(squads)}")
    for k, n in Counter(c["id"] for c in chars).items():
        if n > 1:
            warnings.append(f"Идентификатор {k} встречается {n} раза — персонаж в нескольких отрядах")
    t0 = time.perf_counter()
    prop = player_assets.collect_property(save_dir, faction_sid, names, (qs or {}).get("towns") or [],
                                          aggregate, warnings)
    timer.add("property", t0)
    return {"characters": chars, "squads": squads, "warnings": warnings, "parsed": parsed, "property": prop,
            "files_total": len(files), "files_registered": len(used) if registry is not None else None,
            "excluded_other_faction": excluded, "registry": registry, "quick_save": qs,
            "quick_save_counts": counts}


def collect_stable(save_dir: str, faction_sid: str, names: IndexNames, timer: Timer | None = None,
                   qs: dict[str, Any] | None = None, attempts: int = 3) -> dict[str, Any]:
    """Повторяет чтение, если игра перезаписала сохранение во время выгрузки (автосохранение)."""
    for n in range(attempts):
        before = saves.signature(save_dir)
        try:
            data = collect(save_dir, faction_sid, names, timer, qs)
        except (OSError, ValueError, IndexError, struct.error):
            if n == attempts - 1:
                raise
            time.sleep(2)
            qs = None
            continue
        if saves.signature(save_dir) == before:
            return data
        time.sleep(2)
        qs = None
    raise RuntimeError("Сохранение изменялось во время чтения; повторите после завершения записи")


def load_gamedata(qs: dict[str, Any] | None, warnings: list[str]) -> tuple[IndexNames, dict[str, Any]]:
    """Справочник для набора модов сохранения (quick.save) или, если его нет, data/mods.cfg."""
    act = gamedb.active_files((qs or {}).get("mods") or None)
    if act["missing"]:
        warnings.append(f"Моды из списка {act['source']} не найдены на диске: {', '.join(act['missing'])}; "
                        "их предметы останутся неразрешёнными")
    index, cache = gamedb.load_index(CACHE_DIR, act["files"])
    return IndexNames(index), {"mods_source": act["source"], "mods": act["mods"], "missing": act["missing"]} | cache


def world_block(qs: dict[str, Any] | None) -> dict[str, Any]:
    w = (qs or {}).get("world") or {}
    hour, minute = w.get("time hour"), w.get("time minute")
    return {"day": w.get("time day"),
            "time": f"{hour:02d}:{minute:02d}" if hour is not None and minute is not None else None,
            "money": w.get("player money")}


def build_snapshot(save_dir: str, faction_sid: str, timer: Timer) -> dict[str, Any]:
    t0 = time.perf_counter()
    warnings: list[str] = []
    try:
        qs = saves.read_quick_save(save_dir)
    except Exception as e:  # noqa: BLE001
        qs = None
        warnings.append(f"quick.save не прочитан ({e!r})")
    t0 = timer.add("quick_save", t0)
    names, gd = load_gamedata(qs, warnings)
    timer.add("gamedata", t0)
    data = collect_stable(save_dir, faction_sid, names, timer, qs)
    warnings += data["warnings"]
    t0 = time.perf_counter()
    saved_at = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(save_dir, "quick.save")))
    reg_names = {r["faction_name"] for r in (data["registry"] or {}).values()
                 if r.get("faction_sid") == faction_sid and r.get("faction_name")}
    snap = {
        "format": FORMAT,
        "save": {"name": os.path.basename(save_dir), "path": save_dir,
                 "saved_at": saved_at.isoformat(timespec="seconds"),
                 "kind": "autosave" if os.path.basename(save_dir).startswith("autosave") else
                         "quicksave" if os.path.basename(save_dir) == "quicksave" else "manual"},
        "world": world_block(data["quick_save"]),
        "squads": data["squads"],
        "characters": data["characters"],
        "supplies": party_supplies(data["characters"], names),
        "research": player_assets.research_block((data["quick_save"] or {}).get("research"), names, warnings),
        "property": {"buildings": data["property"]["buildings"], "player_towns": data["property"]["player_towns"]},
        "warnings": warnings,
        "meta": {
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "session_file": saved_at.strftime("%Y-%m-%d_%H-%M-%S") + ".json",
            "player_faction": {"sid": faction_sid, "name": sorted(reg_names)[0] if reg_names else None,
                               "method": "поле 'owner faction ID' в записи состояния персонажа"},
            "source": {"platoon_files": data["files_total"],
                       "registered_in_quick_save": data["files_registered"],
                       "parsed": data["parsed"], "excluded_other_faction": data["excluded_other_faction"],
                       "quick_save_counts": data["quick_save_counts"],
                       "zone_files": data["property"]["source"]["zone_files"],
                       "zone_files_with_property": data["property"]["source"]["zone_files_with_property"]},
            "game_data": gd,
            "unresolved_sids": sorted(names.unresolved),
            "verification": (itemstats.VERIFICATION | CHAR_VERIFICATION | prosthetics.VERIFICATION
                             | player_assets.VERIFICATION),
            "field_notes": FIELD_NOTES,
        },
    }
    timer.add("build", t0)
    return snap


def _write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    os.replace(tmp, path)


def _dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, separators=(",", ":") if indent is None else None)


def previous_session(out_dir: str, session_file: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Ближайшая более ранняя выгрузка того же формата; файлы другого формата пропускаются."""
    skipped = []
    for p in sorted(glob.glob(os.path.join(out_dir, "????-??-??_??-??-??.json")), reverse=True):
        if os.path.basename(p) >= session_file:
            continue
        with open(p, encoding="utf-8") as fh:
            prev = json.load(fh)
        if prev.get("format") in COMPATIBLE_FORMATS:
            return prev, os.path.basename(p), skipped
        skipped.append(os.path.basename(p))
    return None, None, skipped


def earlier_sessions(out_dir: str, before_file: str, min_day: int,
                     limit: int = HISTORY_MAX_SNAPSHOTS) -> list[tuple[str, dict[str, Any]]]:
    """Совместимые выгрузки раньше before_file, от старых к новым: с днём ≥ min_day и одна перед ними —
    исходное состояние первого шага истории."""
    out: list[tuple[str, dict[str, Any]]] = []
    for p in sorted(glob.glob(os.path.join(out_dir, "????-??-??_??-??-??.json")), reverse=True):
        if len(out) >= limit:
            break
        if os.path.basename(p) >= before_file:
            continue
        with open(p, encoding="utf-8") as fh:
            snap = json.load(fh)
        if snap.get("format") not in COMPATIBLE_FORMATS:
            continue
        out.append((os.path.basename(p), snap))
        day = (snap.get("world") or {}).get("day")
        if day is None or day < min_day:
            break
    return out[::-1]


def with_changes(snap: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """Итоговый файл: снимок и сравнение с прошлой выгрузкой сразу после мира — их читают первыми."""
    head = ("format", "save", "world")
    return {k: snap[k] for k in head} | {"changes": diff} | {k: v for k, v in snap.items() if k not in head}


def run(save_dir: str | None, out_dir: str | None, faction_sid: str | None, explain: bool = False) -> int:
    """Снимки и session_diff.json — в out_dir (промежуточные); итоговый kenshi-session.json — в корень проекта,
    а при явном out_dir — в out_dir, чтобы проверочные запуски не перезаписывали основной файл."""
    t_start = time.perf_counter()
    timer = Timer()
    faction_sid = faction_sid or DEFAULT_PLAYER_FACTION
    latest_path = os.path.join(out_dir or PROJECT, LATEST_FILE)
    out_dir = out_dir or os.path.join(PROJECT, "sessions")
    t0 = time.perf_counter()
    if save_dir is None:
        found = saves.list_saves(saves.save_roots())
        if not found:
            print("Сохранения Kenshi не найдены", file=sys.stderr)
            return 2
        save_dir = found[0][1]
    save_dir = os.path.abspath(save_dir)
    timer.add("find_save", t0)

    snap = build_snapshot(save_dir, faction_sid, timer)
    if not snap["characters"]:
        print(f"В сохранении {save_dir} не найдено персонажей фракции {faction_sid}", file=sys.stderr)
        return 3

    os.makedirs(out_dir, exist_ok=True)
    session_file = snap["meta"]["session_file"]
    t0 = time.perf_counter()
    prev, prev_file, skipped = previous_session(out_dir, session_file)
    diff = session_diff.diff(prev, snap, prev_file, explain=explain)
    if skipped:
        diff["skipped_other_format"] = skipped
    t0 = timer.add("diff", t0)
    day = snap["world"]["day"]
    if diff.get("valid") and day is not None:
        chain = earlier_sessions(out_dir, prev_file, day - HISTORY_DAYS) + [(prev_file, prev)]
        diff["history"] = {"days": HISTORY_DAYS} | session_diff.history(chain)
        t0 = timer.add("history", t0)
    # Сессия без отступов: файл передаётся в чат-ИИ модели, лишние пробелы — лишние токены.
    # timings_ms фиксируются до сериализации; её время и размер файла выводятся в консоль.
    snap["meta"]["timings_ms"] = {k: round(v, 1) for k, v in timer.ms.items()}
    body = _dumps(snap)
    latest = _dumps(with_changes(snap, diff))
    t0 = timer.add("serialize", t0)
    size = len(latest.encode("utf-8"))
    _write_text(os.path.join(out_dir, session_file), body)
    _write_text(os.path.join(out_dir, "session_diff.json"), _dumps(diff, None if not explain else 1))
    _write_text(latest_path, latest)
    timer.add("write", t0)

    print(f"Сохранение: {snap['save']['name']} ({snap['save']['saved_at']}), день {snap['world']['day']}, "
          f"{snap['world']['time']}")
    print(f"Персонажей: {len(snap['characters'])} в {len(snap['squads'])} отрядах; "
          f"справочник: {snap['meta']['game_data']['cache']} ({snap['meta']['game_data']['mods_source']})")
    print(f"Файл для чат-ИИ моделей: {latest_path} ({size / 1024:.0f} КБ); снимок: {os.path.join(out_dir, session_file)}")
    print("Сравнение: " + session_diff.summary_line(diff))
    if "history" in diff:
        print(f"История за {HISTORY_DAYS} игровых дней: " + session_diff.history_line(diff["history"]))
    if explain:
        session_diff.print_explain(diff)
    for w in snap["warnings"]:
        print("Предупреждение:", w)
    if snap["meta"]["unresolved_sids"]:
        print(f"Не найдено в данных игры: {len(snap['meta']['unresolved_sids'])} sid (см. meta.unresolved_sids)")
    ms = {k: round(v) for k, v in timer.ms.items()}
    print(f"Время: {time.perf_counter() - t_start:.2f} с ({', '.join(f'{k} {v} мс' for k, v in ms.items())})")
    return 0


def run_diff(a_path: str, b_path: str, explain: bool) -> int:
    """Сравнение двух произвольных выгрузок (диагностика)."""
    with open(a_path, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(b_path, encoding="utf-8") as fh:
        b = json.load(fh)
    d = session_diff.diff(a, b, os.path.basename(a_path), explain=explain)
    print(json.dumps(d, ensure_ascii=False, indent=1))
    if explain:
        session_diff.print_explain(d)
    return 0 if d.get("valid", True) else 4
