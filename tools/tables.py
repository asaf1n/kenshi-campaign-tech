"""Табличные выгрузки published/tables/: CSV и index.md из снимка, locations.json и справочников игры.

Чистые функции без обращения к диску (кроме load_game_data): одинаковые входы дают одинаковые байты.
Колонки calc_* в equipment.csv - значения по формулам itemstats.py/prosthetics.py; при calc_status = verified они
совпадают с карточкой игры (броня, оружие ближнего боя, арбалет, протез; границы - CALC_NOTE). Остальные колонки - исходные значения
сохранения и шаблонов игры. Колонок цены в calc_* нет: цены - только item_prices.csv.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools import itemstats, prosthetics
from tools.world_context import ZONE_KEY_FORMAT, _building_places, _clean_handle_tuple, _settlement, storage_rows, zone_settlements

TABLES_DIR: str = "tables"
INDEX_NAME: str = "index.md"
INDEX_ARTIFACT: str = f"{TABLES_DIR}/{INDEX_NAME}"
# Строка таблицы файлов в index.md: валидация сверяет число строк CSV с этим значением.
INDEX_ROW_RE = re.compile(r"^\| `([a-z_]+\.csv)` \| (\d+) \|", re.MULTILINE)
INDEX_SNAPSHOT_RE = re.compile(r"^- snapshot_id: `([^`]+)`", re.MULTILINE)

# Слоты предметов в items[] снимка (session_export.WEAPON_SLOTS / ARMOUR_SLOTS).
WEAPON_SECTIONS: dict[str, str] = {"back": "weapon_back", "hip": "weapon_hip"}
ARMOUR_SECTIONS: dict[str, str] = {"head": "head", "shirt": "shirt", "armour": "body", "legs": "legs", "boots": "boots"}

# Русские подписи навыков и атрибутов: locale/ru_RU/LC_MESSAGES/main.po установленной игры.
SKILL_LABELS: dict[str, str] = {
    "Armour Smithing": "Ковка доспехов", "Assassination": "Убийство", "Athletics": "Атлетика",
    "Blunt": "Дробящее", "Cooking": "Готовка", "Crossbow Smithing": "Ковка арбалетов",
    "Crossbows": "Арбалеты", "Dexterity": "Ловкость", "Dodge": "Уворот", "Engineering": "Инженерия",
    "Farming": "Фермерство", "Field Medic": "Полевой медик", "Hackers": "Рубачи",
    "Heavy Weapons": "Тяжелое оружие", "Katanas": "Катаны", "Labouring": "Работа", "Lockpicking": "Взлом",
    "Martial Arts": "Боевые искусства", "Melee Attack": "Атака (ближний бой)",
    "Melee Defence": "Защита (ближний бой)", "Perception": "Восприятие", "Polearms": "Древковое",
    "Precision Shooting": "Точная стрельба", "Robotics": "Роботехника", "Sabres": "Сабли",
    "Science": "Наука", "Stealth": "Скрытность", "Strength": "Сила", "Swimming": "Плавание",
    "Thievery": "Воровство", "Toughness": "Крепость", "Turrets": "Турели", "Weapon Smithing": "Ковка оружия",
}
ATTRIBUTES: tuple[str, ...] = ("Dexterity", "Perception", "Strength", "Toughness")

# Поля шаблонов игры (ключи индекса gamedb) для equipment.csv. «value» - в item_prices.csv.
WEAPON_TPL: tuple[str, ...] = (
    "cut damage multiplier", "blunt damage multiplier", "weight kg", "weight mult", "armour penetration",
    "attack mod", "defence mod", "indoors mod", "bleed mult", "animal damage mult", "human damage mult",
    "robot damage mult", "skill category")
CROSSBOW_TPL: tuple[str, ...] = (
    "pierce damage min 0", "pierce damage min 1", "pierce damage max 0", "pierce damage max 1",
    "reload time min", "reload time min 1", "reload time max", "reload time max 1",
    "accuracy deviation at 0 skill", "accuracy deviation at 0 skill 1", "range", "range 1",
    "shot speed", "shot speed 1", "accuracy perfect skill", "weight kg")
ARMOUR_TPL: tuple[str, ...] = (
    "class", "material type", "slot", "cut into stun", "level bonus", "blunt def bonus", "cut def bonus",
    "pierce def mult", "weight kg", "weather protection amount", "weather protection0", "stealth mult",
    "ranged skill mult", "dodge mult", "dexterity mult", "asassination mult", "athletics mult",
    "combat speed mult", "damage output mult", "fist injury mult", "unarmed bonus", "perception bonus",
    "combat attk bonus", "combat def bonus", "combat skill bonus", "ranged bonus")
LIMB_TPL: tuple[str, ...] = (
    "slot", "weight kg", "HP", "HP 1", "strength mult", "strength mult 1", "dexterity mult",
    "dexterity mult 1", "athletics mult", "athletics mult 1", "swimming mult", "swimming mult 1",
    "stealth mult", "stealth mult 1", "thievery mult", "thievery mult 1", "ranged mult", "ranged mult 1",
    "unarmed damage bonus", "unarmed damage bonus 1", "combat speed mult", "encumbrance mult", "overall mult")
MANUFACTURER_FIELDS: tuple[tuple[str, str], ...] = (
    ("cut damage mod", "manufacturer_cut_damage_mod"), ("blunt damage mod", "manufacturer_blunt_damage_mod"),
    ("weight mod", "manufacturer_weight_mod"))
GRADE_FIELDS: tuple[tuple[str, str], ...] = (
    ("attack mod", "grade_attack_mod"), ("defence mod", "grade_defence_mod"))
# Итоговые колонки equipment.csv. Порядок фиксирован; пусто - level неизвестен или нет данных шаблона.
CALC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("calc_status", "Причина пустых calc_*: verified - рассчитано (сочетание сверено с карточками игры); unknown_level - level не "
                    "определён; unknown_template - шаблон (или константы игры) не найден в справочнике; unverified_class - "
                    "сочетание без карточки игры (броня: класс x материал; оружие: класс x производитель; протез не на ноге)"),
    ("calc_level", "level, использованный для расчёта calc_*; пусто - level неизвестен, все calc_* пусты"),
    ("calc_cut_damage", "Оружие ближнего боя: режущий урон (`cut damage multiplier` x `cut damage mod` x (0.3 + 0.017 x level))"),
    ("calc_blunt_damage", "Оружие ближнего боя: дробящий урон (`blunt damage multiplier` x `blunt damage mod` x level / 50)"),
    ("calc_damage_min", "Арбалет: нижняя граница урона, без округления (карточка усекает до целого)"),
    ("calc_damage_max", "Арбалет: верхняя граница урона, без округления (карточка округляет до целого: 100.62 -> 101)"),
    ("calc_reload_min_s", "Арбалет: перезарядка, нижняя граница, с"),
    ("calc_reload_max_s", "Арбалет: перезарядка, верхняя граница, с"),
    ("calc_spread_deg", "Арбалет: разброс, градусы"),
    ("calc_range_m", "Арбалет: дальность, м"),
    ("calc_shot_speed_ms", "Арбалет: скорость выстрела, м/с, без округления (lerp(`shot speed`, `shot speed 1`, level/100) x 0.23; карточка округляет)"),
    ("calc_weight_kg", "Вес предмета, кг (оружие ближнего боя, броня); у арбалета и протеза - `tpl_weight_kg`"),
    ("calc_cut_resist_pct", "Броня: сопротивление разрезу, %"),
    ("calc_blunt_resist_pct", "Броня: сопротивление дробящему, %"),
    ("calc_harpoon_resist", "Броня: сопротивление гарпунам"),
    ("calc_cut_efficiency", "Броня: эффективность сопротивления разрезу (1 - `cut into stun`)"),
    ("calc_stealth_mult", "Броня и протез: множитель скрытности; 1 - нейтрально"),
    ("calc_ranged_mult", "Броня: множитель дальнего боя (арбалеты); 1 - нейтрально"),
    ("calc_dodge_mult", "Броня: множитель уворота; 1 - нейтрально"),
    ("calc_dexterity_mult", "Броня: множитель ловкости; 1 - нейтрально"),
    ("calc_combat_speed_mult", "Броня: множитель скорости боя; 1 - нейтрально"),
    ("calc_athletics_mult", "Броня и протез: множитель атлетики; 1 - нейтрально, у брони считается для ног и обуви"),
    ("calc_martial_arts_add", "Броня: поправка боевых искусств; 0 - нейтрально"),
    ("calc_swimming_mult", "Протез: множитель плавания; не округлён, UI показывает целые проценты усечением"),
    ("calc_hp_max", "Протез: ОЗ макс (округление до 5)"),
)
CALC_NOTE: str = (
    "Колонки `calc_*` в `equipment.csv` - значения по формулам конвертера (itemstats.py, prosthetics.py). "
    "`calc_status` = verified означает, что в строке сверены с карточкой игры все публикуемые `calc_*`; при остальных "
    "статусах все `calc_*` пусты, а `calc_status` объясняет причину. Значения публикуются без округления; совпадение с "
    "карточкой - равенство после округления до точности интерфейса (усечение целой части для сопротивлений, веса и "
    "нижней границы урона арбалета; округление для верхней границы урона и скорости выстрела); в CSV числа записываются "
    "с точностью до 6 знаков без хвостовых нулей (это не округление интерфейса); шаблоны берутся после применения "
    "модов снимка. verified только для сверенных сочетаний: броня - лёгкий и средний класс x кожа, кольчуга; средний x "
    "латы; тяжёлый x латы; оружие ближнего боя - катаны (Идущие по Клинку, Скелеты-кузнецы), сабли (Идущие по Клинку, "
    "Крест, Скелеты-кузнецы), древковое (Идущие по Клинку, Крест), тяжёлое (Катунский Кулибин), рубачи (Идущие по Клинку, "
    "Крест); арбалеты; протезы ног. Прочие сочетания получают `unverified_class`: в частности вес не сошёлся у "
    "тяжёлого оружия Скелетов-кузнецов (Падающее солнце: карточка 23, расчёт 24.0), рубачей Древних (карточка 5, расчёт 6) "
    "и катан Катунского Кулибина (карточка 9, расчёт 8.0). Колонок цены в `calc_*` нет: базовые цены - в `item_prices.csv`. "
    "Не подтверждено: порог скрытия атлетики на карточке; граница 59.x у брони Высокого уровня (подбор level; blunt и "
    "гарпун карточки на 1 ниже расчёта при level 60); грейды брони 5 «Прото» и 20 «Низкий»; множители протезов "
    "публикуются только для атлетики, плавания и скрытности."
)
REACTION_CONDITIONS: tuple[str, ...] = ("NPC is", "NPC is NOT", "player ally", "player enemy")


def tpl_column(field: str) -> str:
    return "tpl_" + field.replace(" ", "_")


@dataclass
class Table:
    name: str
    sort_key: str
    purpose: str
    columns: list[tuple[str, str]]
    rows: list[dict[str, Any]]


def cell(value: Any) -> str:
    """Значение ячейки: пустое - отсутствие; числа без локали и без хвостовых нулей; булевы true/false."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        text = f"{round(value, 6):.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text
    return str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def csv_bytes(table: Table) -> bytes:
    names = [c for c, _ in table.columns]
    known = set(names)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(names)
    for row in table.rows:
        extra = set(row) - known
        if extra:
            raise ValueError(f"{table.name}: колонки вне словаря: {sorted(extra)}")
        writer.writerow([cell(row.get(c)) for c in names])
    return out.getvalue().encode("utf-8")


def _stats(index: dict[str, list[Any]] | None, sid: str | None) -> dict[str, Any]:
    entry = (index or {}).get(sid or "")
    return (entry[4] if entry and len(entry) > 4 else None) or {}


def _names(index: dict[str, list[Any]] | None, sid: str | None) -> tuple[str | None, str | None]:
    """(name_en, name_ru) записи справочника игры; для sid вне справочника - (None, None)."""
    entry = (index or {}).get(sid or "")
    return (entry[0] or None, entry[1] or None) if entry else (None, None)


def _label(index: dict[str, list[Any]] | None, sid: str | None) -> str | None:
    en, ru = _names(index, sid)
    return ru or en


def _join(values: Iterable[Any]) -> str | None:
    items = [str(v) for v in values if v not in (None, "")]
    return "; ".join(items) if items else None


def _active(flags: Any) -> str | None:
    return _join(sorted(k for k, v in (flags or {}).items() if v))


# --------------------------------------------------------------------------- отряд

def squad_table(current: dict[str, Any], locations: dict[str, Any] | None) -> Table:
    characters = current.get("characters") or []
    places = {c.get("id"): c for c in (locations or {}).get("characters", [])}
    squad_names = {s.get("squad_id"): s.get("squad_name") for s in current.get("squads") or []}
    skills = sorted({k for c in characters for k in (c.get("skills_base") or {}) if k not in ATTRIBUTES})
    attrs = sorted({k for c in characters for k in (c.get("attributes_base") or {})} | set(ATTRIBUTES))

    rows = []
    for c in characters:
        place = places.get(c.get("id")) or {}
        loc, near = place.get("location") or {}, (place.get("nearby_locations") or [None])[0] or {}
        cond, jobs, carrying = c.get("condition") or {}, c.get("jobs") or {}, c.get("carrying") or {}

        def task(t: dict[str, Any]) -> str:
            base = t.get("task_name") or str(t.get("task_code"))
            return f"{base} [{t['target_name']}]" if t.get("target_name") else base

        limbs = c.get("lost_limbs") or []
        prosthetics = [
            f"{p.get('limb')}: {p.get('name') or p.get('sid')} (level {p.get('level')}, {p.get('grade')})"
            for p in sorted(c.get("prosthetics") or [], key=lambda p: str(p.get("limb")))
        ]
        row: dict[str, Any] = {
            "character_id": c.get("id"), "name": c.get("name"), "race": c.get("race"),
            "is_animal": c.get("is_animal"), "is_leader": c.get("is_leader"), "female": c.get("female"),
            "squad_id": c.get("squad_id"), "squad_name": c.get("squad_name") or squad_names.get(c.get("squad_id")),
            "zone": place.get("zone"), "resolution_status": place.get("resolution_status"),
            "region": (place.get("region") or {}).get("name_ru") or (place.get("region") or {}).get("name_en"),
            "settlement": loc.get("name_ru") or loc.get("name_en"),
            "nearby_settlement": near.get("name_ru") or near.get("name_en"),
            "status_active": _active(c.get("status")), "combat_modes_active": _active(c.get("combat_modes")),
            "blood": cond.get("blood"), "hunger_raw": cond.get("hunger_raw"),
            "bleeding": cond.get("bleeding"), "ko": cond.get("KO"),
            "lost_limbs": _join(l.get("limb") if isinstance(l, dict) else l for l in limbs),
            "prosthetics": _join(prosthetics),
            "jobs_enabled": jobs.get("enabled") if jobs else None,
            "jobs_primary": _join(task(t) for t in jobs.get("primary") or [] if isinstance(t, dict)),
            "jobs_temporary": _join(task(t) for t in jobs.get("temporary") or [] if isinstance(t, dict)),
            "carrying_target": (carrying.get("target_name") or "цель без имени") if carrying.get("is_carrying") else None,
            "carried_by": carrying.get("carried_by_name") or ("персонаж без имени" if carrying.get("carried_by") else None),
            "bounty_total": (c.get("bounties") or {}).get("total_amount"),
        }
        for k, v in {**(c.get("attributes_base") or {}), **(c.get("skills_base") or {})}.items():
            row[k] = v
        rows.append(row)
    rows.sort(key=lambda r: (str(r["character_id"]), str(r["name"])))

    columns = [
        ("character_id", "Стабильный ID персонажа (`ps:` + portrait_serial, при отсутствии `h:`)"),
        ("name", "Имя"), ("race", "Раса"), ("is_animal", "Животное"), ("is_leader", "Лидер отряда"),
        ("female", "Пол женский"), ("squad_id", "ID отряда"), ("squad_name", "Название отряда"),
        ("zone", "Зона карты `x.y` из locations.json"),
        ("resolution_status", "Способ определения места: in_location, near_location, in_region, unknown"),
        ("region", "Регион карты"), ("settlement", "Поселение, в границах которого находится персонаж"),
        ("nearby_settlement", "Ближайшее поселение при near_location"),
        ("status_active", "Активные статусы (coma, dead, incapacitated, unconscious), через `; `"),
        ("combat_modes_active", "Включённые боевые режимы, через `; `"),
        ("blood", "Кровь"), ("hunger_raw", "Сытость, сырое значение сохранения"),
        ("bleeding", "Кровотечение"), ("ko", "Нокаут (KO)"),
        ("lost_limbs", "Потерянные конечности; пусто - поле в снимке отсутствует (нет потерянных)"),
        ("prosthetics", "Протезы: `слот: имя (level N, grade)`, через `; `; подробности - equipment.csv"),
        ("jobs_enabled", "Работы включены"),
        ("jobs_primary", "Основные работы `TASK [цель]` по порядку слотов, через `; `"),
        ("jobs_temporary", "Временные работы, через `; `"),
        ("carrying_target", "Кого несёт персонаж"), ("carried_by", "Кто несёт персонажа"),
        ("bounty_total", "Сумма наград за голову"),
    ]
    columns += [(a, f"Атрибут «{SKILL_LABELS.get(a, a)}», базовое значение") for a in attrs]
    columns += [(s, f"Навык «{SKILL_LABELS.get(s, s)}», базовое значение") for s in skills]
    return Table("squad.csv", "character_id", "Один персонаж отряда игрока в строке", columns, rows)


# --------------------------------------------------------------------------- экипировка

def _tpl(row: dict[str, Any], stats: dict[str, Any], fields: Sequence[str]) -> None:
    for f in fields:
        if f in stats:
            row[tpl_column(f)] = stats[f]


def _constants(index: dict[str, list[Any]] | None) -> dict[str, Any]:
    for entry in (index or {}).values():
        if entry[2] == "CONSTANTS" and len(entry) > 4 and entry[4]:
            return entry[4]
    return {}


def _gate(level: Any, template: dict[str, Any], confirmed: bool) -> str:
    """calc_status: причина, по которой calc_* пусты, либо verified."""
    if level is None:
        return "unknown_level"
    if not template:
        return "unknown_template"
    return "verified" if confirmed else "unverified_class"


def calc_weapon(w: dict[str, Any], t: dict[str, Any], manuf: dict[str, Any], const: dict[str, Any],
                manuf_name: str | None = None) -> dict[str, Any]:
    level = w.get("level")
    crossbow = w.get("type") == "crossbow"
    confirmed = crossbow or (t.get("skill category"), manuf_name) in itemstats.CONFIRMED_MELEE_COMBOS
    status = _gate(level, t, confirmed)
    if status != "verified":
        return {"calc_status": status}
    if not crossbow:
        cut, blunt = itemstats.melee_damage(t, manuf, level)
        weight = itemstats.melee_weight(t, blunt, const.get("weapon inventory weight mult", 0.5))
        return {"calc_status": status, "calc_level": level, "calc_cut_damage": cut, "calc_blunt_damage": blunt,
                "calc_weight_kg": weight}
    if "bow damage 1" not in const or "bow damage 99" not in const:
        return {"calc_status": "unknown_template"}  # без констант игры урон арбалета не определён
    stats = itemstats.crossbow_stats(t, level, const, raw=True)
    damage = stats.get("damage") or [None, None]  # без округления интерфейса
    reload_s = stats.get("reload_s") or [None, None]
    return {"calc_status": status, "calc_level": level, "calc_damage_min": damage[0], "calc_damage_max": damage[1],
            "calc_reload_min_s": reload_s[0], "calc_reload_max_s": reload_s[1],
            "calc_spread_deg": stats.get("spread_deg"), "calc_range_m": stats.get("range_m"),
            "calc_shot_speed_ms": stats.get("shot_speed_ms")}


# Колонка -> (ключ skill_modifiers armour_view, поле шаблона, нейтральное значение).
ARMOUR_CALC_MODS: tuple[tuple[str, str, str, float], ...] = (
    ("calc_stealth_mult", "stealth_multiplier", "stealth mult", 1.0),
    ("calc_ranged_mult", "ranged_multiplier", "ranged skill mult", 1.0),
    ("calc_dodge_mult", "dodge_multiplier", "dodge mult", 1.0),
    ("calc_dexterity_mult", "dexterity_multiplier", "dexterity mult", 1.0),
    ("calc_combat_speed_mult", "combat_speed_multiplier", "combat speed mult", 1.0),
    ("calc_athletics_mult", "athletics_multiplier", "athletics mult", 1.0),
    ("calc_martial_arts_add", "martial_arts_additive", "unarmed bonus", 0.0),
)


def calc_armour(a: dict[str, Any], t: dict[str, Any], index: dict[str, list[Any]] | None) -> dict[str, Any]:
    level = a.get("level")
    status = _gate(level, t, (t.get("class"), t.get("material type")) in itemstats.CONFIRMED_ARMOUR_COMBOS)
    if status != "verified":
        return {"calc_status": status}
    view = itemstats.armour_view(t, level, lambda sid: sid,
                                 lambda sid: ((index or {}).get(sid) or [None])[0] == "Chest")
    res, mods = view.get("resistances") or {}, view.get("skill_modifiers") or {}
    out: dict[str, Any] = {
        "calc_status": status, "calc_level": level, "calc_cut_resist_pct": res.get("cut_pct"), "calc_blunt_resist_pct": res.get("blunt_pct"),
        "calc_harpoon_resist": res.get("harpoon"), "calc_cut_efficiency": view.get("cut_efficiency"),
        "calc_weight_kg": view.get("weight_kg"),
    }
    for col, key, field, neutral in ARMOUR_CALC_MODS:
        if key in mods:
            out[col] = mods[key]
        elif t.get(field, neutral) >= 0:  # armour_view опускает нейтральные; отрицательные шаблонные игра не показывает
            out[col] = neutral
    return out


def calc_prosthetic(p: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    level = p.get("level")
    status = _gate(level, t, p.get("limb") in itemstats.CONFIRMED_PROSTHETIC_LIMBS)
    if status != "verified":
        return {"calc_status": status}
    out: dict[str, Any] = {"calc_status": status, "calc_level": level, "calc_hp_max": prosthetics.hp_max(t, level)}
    for col, field in (("calc_stealth_mult", "stealth mult"), ("calc_athletics_mult", "athletics mult"),
                       ("calc_swimming_mult", "swimming mult")):
        lo = t.get(field, 1.0)
        out[col] = itemstats.lerp(lo, t.get(field + " 1", lo), level / 100)
    return out


GEAR_TYPES: frozenset[str] = frozenset({"ARMOUR", "WEAPON", "CROSSBOW", "LIMB_REPLACEMENT"})
# Значение шаблона `slot` брони (itemstats.SLOT_*) -> имя слота, как у надетой брони.
ARMOUR_SLOT_NAMES: dict[int, str] = {itemstats.SLOT_HAT: "head", itemstats.SLOT_SHIRT: "shirt", itemstats.SLOT_BODY: "body",
                                     itemstats.SLOT_LEGS: "legs", itemstats.SLOT_BOOTS: "boots"}
# Раздел предмета в items[] -> (контейнер inventory.csv, слот). Предметы внутри рюкзака (container `inventory>...`)
# получают контейнер backpack и слот из раздела (backpack_content).
SECTION_PLACES: dict[str, tuple[str, str]] = {
    "back": ("body", "weapon_back"), "hip": ("body", "weapon_hip"), "head": ("body", "head"), "shirt": ("body", "shirt"),
    "armour": ("body", "body"), "legs": ("body", "legs"), "boots": ("body", "boots"),
    "backpack_attach": ("body", "backpack"), "backpack": ("body", "backpack"),
    "belt": ("belt", "belt"), "main": ("inventory", "main"),
}
CONTAINER_ORDER: tuple[str, ...] = ("body", "belt", "backpack", "inventory", "unknown")
LOCATION_ORDER: tuple[str, ...] = ("worn", "other_slot", "belt", "inventory", "backpack", "storage")


def _instance_grade(index: dict[str, list[Any]] | None, sid: str | None, level: Any, material_sid: str | None) -> str | None:
    """Грейд экземпляра: оружие - запись material, броня и арбалет - по уровню (itemstats), протез - prosthetics."""
    kind = _type_of(index, sid or "")
    if kind == "WEAPON":
        return _label(index, material_sid) if material_sid else None
    if kind in ("ARMOUR", "CROSSBOW"):
        return itemstats.grade_for(kind, level)
    if kind == "LIMB_REPLACEMENT" and level is not None:
        return prosthetics.CONFIRMED_GRADES.get(level)
    return None


def _spare_entry(index: dict[str, list[Any]] | None, sid: str, level: Any, stolen: bool,
                 material_sid: str | None, company_sid: str | None) -> tuple[str, dict[str, Any]]:
    """Запись предмета вне слотов (инвентарь, рюкзак, склад) в виде записей надетого: (kind, entry)."""
    kind = _type_of(index, sid)
    en, ru = _names(index, sid)
    entry: dict[str, Any] = {"name": ru or en, "name_en": en if en != ru else None, "sid": sid, "level": level}
    if stolen:
        entry["stolen"] = True
    t = _stats(index, sid)
    if kind == "WEAPON":
        entry |= {"type": "melee", "grade": _label(index, material_sid) if material_sid else None,
                  "manufacturer": _label(index, company_sid) if company_sid else None}
        return "weapon", entry
    if kind == "CROSSBOW":
        return "weapon", entry | {"type": "crossbow", "grade": itemstats.grade_for("CROSSBOW", level)}
    if kind == "ARMOUR":
        view = itemstats.armour_view(t, level, lambda s: _label(index, s) or s, lambda s: _names(index, s)[0] == "Chest")
        return "armour", entry | view | {"slot": ARMOUR_SLOT_NAMES.get(t.get("slot"))}
    slot = t.get("slot")
    limb = prosthetics.LIMBS[slot - prosthetics.LIMB_SLOT_BASE] if (
        isinstance(slot, int) and 0 <= slot - prosthetics.LIMB_SLOT_BASE < len(prosthetics.LIMBS)) else None
    return "prosthetic", entry | {"limb": limb, "grade": _instance_grade(index, sid, level, None)}


def _gear_row(owner: dict[str, Any], kind: str, entry: dict[str, Any], slot: str | None, inst: dict[str, Any],
              index: dict[str, list[Any]] | None, const: dict[str, Any]) -> dict[str, Any]:
    """Строка equipment.csv: общая часть, значения шаблона, множители и calc_* (одинаково для надетого, запасного и складского)."""
    row: dict[str, Any] = {
        **owner, "kind": kind, "slot": slot, "item_name": entry.get("name"), "item_name_en": entry.get("name_en"),
        "sid": entry.get("sid"), "item_type": entry.get("type"), "level": entry.get("level"),
        "quality": inst.get("quality"), "grade": entry.get("grade"), "stolen": entry.get("stolen"),
    }
    stats = _stats(index, entry.get("sid"))
    if kind == "weapon":
        crossbow = entry.get("type") == "crossbow"
        _tpl(row, stats, CROSSBOW_TPL if crossbow else WEAPON_TPL)
        manuf: dict[str, Any] = {}
        if not crossbow:
            row["grade_sid"], row["manufacturer"] = inst.get("material_sid") or None, entry.get("manufacturer")
            row["manufacturer_sid"] = inst.get("company_sid") or None
            for field, col in GRADE_FIELDS:
                row[col] = _stats(index, row["grade_sid"]).get(field)
            manuf = _stats(index, row["manufacturer_sid"])
            for field, col in MANUFACTURER_FIELDS:
                row[col] = manuf.get(field)
        row |= calc_weapon(entry, stats, manuf, const, _names(index, row.get("manufacturer_sid"))[0])
    elif kind == "armour":
        row["item_type"] = None
        row["armour_class"], row["armour_material"] = entry.get("class"), entry.get("material")
        cov = entry.get("coverage") or {}
        row["coverage"] = _join(f"{k}={cell(v)}" for k, v in sorted(cov.items()))
        row["template_unshown"] = _join(f"{k}={cell(v)}" for k, v in sorted((entry.get("template_unshown") or {}).items()))
        _tpl(row, stats, ARMOUR_TPL)
        for col, cat in (("tpl_races", "refs:races"), ("tpl_races_exclude", "refs:races exclude")):
            row[col] = _join(_label(index, s) or s for s in sorted(stats.get(cat) or {}))
        row |= calc_armour(entry, stats, index)
    else:
        _tpl(row, stats, LIMB_TPL)
        row |= calc_prosthetic({"limb": entry.get("limb"), "level": entry.get("level")}, stats)
    return row


def equipment_table(current: dict[str, Any], index: dict[str, list[Any]] | None,
                    locations: dict[str, Any] | None = None, zone_places: dict[str, str] | None = None) -> Table:
    rows: list[dict[str, Any]] = []
    const = _constants(index)
    for c in current.get("characters") or []:
        owner = {"character_id": c.get("id"), "character_name": c.get("name")}
        # Количественные поля экземпляра (quality, sid грейда и производителя) берутся из items[] по слоту и sid.
        instances: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for it in c.get("items") or []:
            slot = WEAPON_SECTIONS.get(it.get("section")) or ARMOUR_SECTIONS.get(it.get("section"))
            if slot and it.get("container") == "inventory":
                instances[(slot, it.get("base_data_sid"))].append(it)
        used: set[int] = set()

        def take(slot: str, sid: str | None) -> dict[str, Any]:
            queue = instances.get((slot, sid or ""))
            if not queue:
                return {}
            inst = queue.pop(0)
            used.add(id(inst))
            return inst

        def worn(kind: str, slot: str, entry: dict[str, Any], inst: dict[str, Any]) -> None:
            row = _gear_row(owner, kind, entry, slot, inst, index, const)
            row["location_kind"], row["quantity"] = "worn", inst.get("quantity")
            rows.append(row)

        for w in c.get("weapons") or []:
            worn("weapon", w.get("slot"), w, take(w.get("slot"), w.get("sid")))
        for slot, a in (c.get("armour") or {}).items():
            if isinstance(a, dict):
                worn("armour", a.get("slot") or slot, a, take(a.get("slot") or slot, a.get("sid")))
        for p in c.get("prosthetics") or []:
            worn("prosthetic", p.get("limb"), p, {})
        # Остальные экземпляры брони, оружия и протезов персонажа: запасные в инвентаре, рюкзаке, на поясе.
        by_item_sid = {it.get("item_sid"): it for it in c.get("items") or []}
        for it in c.get("items") or []:
            sid = it.get("base_data_sid")
            if id(it) in used or _type_of(index, sid or "") not in GEAR_TYPES:
                continue
            cont, sec = it.get("container") or "", it.get("section")
            holder = ""
            if cont.startswith("inventory>"):
                kind_loc = "backpack"
                parent = by_item_sid.get(cont.rsplit(">", 1)[1]) or {}
                holder = _label(index, parent.get("base_data_sid")) or ""
            elif sec == "belt":
                kind_loc = "belt"
            elif sec == "main":
                kind_loc = "inventory"
            else:
                kind_loc = "other_slot"  # предмет в слоте надетого сверх показанного в weapons/armour
            kind, entry = _spare_entry(index, sid, it.get("level"), bool(it.get("is_stolen")),
                                       it.get("material_sid") or None, it.get("company_sid") or None)
            row = _gear_row(owner, kind, entry, entry.get("slot") or entry.get("limb"), it, index, const)
            row |= {"location_kind": kind_loc, "container": holder or None, "quantity": it.get("quantity")}
            rows.append(row)
    # Склады: все экземпляры брони, оружия и протезов в зданиях игрока.
    srows, _nodes = storage_rows((current.get("property") or {}).get("buildings") or [], locations, index, zone_places)
    for r in srows:
        if _type_of(index, r["base_sid"] or "") not in GEAR_TYPES:
            continue
        kind, entry = _spare_entry(index, r["base_sid"], r["level"], r["stolen"], r["material_sid"], r["company_sid"])
        row = _gear_row({}, kind, entry, entry.get("slot") or entry.get("limb"), r, index, const)
        row |= {"location_kind": "storage", "container": r["container"], "quantity": r["quantity"],
                "building_id": r["building_id"], "building": r["building"], "settlement": r["settlement"], "zone": r["zone"]}
        rows.append(row)

    def order(r: dict[str, Any]) -> tuple[Any, ...]:
        where = (str(r.get("settlement") or "~"), str(r.get("zone")), str(r.get("building_id")), str(r.get("container")))
        return (LOCATION_ORDER.index(r["location_kind"]), str(r.get("character_id") or ""),
                where if r["location_kind"] == "storage" else (), r["kind"], str(r["slot"]), str(r["sid"]),
                r["level"] if isinstance(r["level"], (int, float)) else -1, str(cell(r.get("quality"))))
    rows.sort(key=order)

    tpl_fields = list(dict.fromkeys([*WEAPON_TPL, *CROSSBOW_TPL, *ARMOUR_TPL, *LIMB_TPL]))
    columns = [
        ("character_id", "ID персонажа; пусто у складских предметов"), ("character_name", "Имя персонажа"),
        ("kind", "weapon, armour или prosthetic"),
        ("slot", "weapon_back/weapon_hip, head/shirt/body/legs/boots или конечность протеза (left_leg...); у запасных и "
                 "складских - слот или конечность по шаблону предмета, у оружия пусто"),
        ("item_name", "Название (ru)"), ("item_name_en", "Название (en), если отличается"),
        ("sid", "sid записи предмета в данных игры"), ("item_type", "melee или crossbow для оружия"),
        ("level", "Уровень экземпляра"), ("quality", "quality экземпляра из items[] (оружие и броня)"),
        ("grade", "Грейд: у оружия - запись material (Мейто, Катун No.N...), у брони и протезов - по уровню"),
        ("stolen", "Предмет помечен как краденый"),
        ("grade_sid", "sid записи грейда оружия"), ("manufacturer", "Производитель оружия (запись company)"),
        ("manufacturer_sid", "sid производителя"),
        ("armour_class", "Класс брони (cloth, light, medium, heavy)"),
        ("armour_material", "Материал брони (cloth, leather, chain, plate)"),
        ("coverage", "Покрытие частей тела `часть=доля`, через `; `"),
        ("template_unshown", "Поля шаблона брони, не показываемые игрой, `поле=значение`"),
    ]
    columns += [(col, f"Множитель/поправка производителя `{f}`") for f, col in MANUFACTURER_FIELDS]
    columns += [(col, f"Поправка грейда (запись material) `{f}`") for f, col in GRADE_FIELDS]
    columns += [(tpl_column(f), f"Поле шаблона игры `{f}`; пусто - не применимо к виду предмета")
                for f in tpl_fields]
    columns += list(CALC_COLUMNS)
    columns += [
        ("location_kind", "Где экземпляр: worn - надето (строки прежнего вида; протез установлен), inventory - основной "
                          "инвентарь персонажа, backpack - внутри рюкзака, belt - пояс, storage - склад здания, "
                          "other_slot - второй предмет в слоте надетого (не показан в weapons/armour)"),
        ("quantity", "Количество в стопке; у надетого - из items[], у протезов пусто"),
        ("container", "backpack: название рюкзака; storage: контейнер внутри здания (как в storage.csv); иначе пусто"),
        ("building_id", "storage: ID здания в сохранении"), ("building", "storage: название здания"),
        ("settlement", "storage: поселение; пусто, если не определено"), ("zone", "storage: зона карты"),
        ("tpl_races", "Брони: расы, только которым разрешено её надевать (`races`); пусто - ограничения нет"),
        ("tpl_races_exclude", "Брони: расы, которым запрещено её надевать (`races exclude`)"),
    ]
    return Table("equipment.csv", "location_kind, character_id / здание, kind, slot, sid, level, quality",
                 "Все экземпляры оружия, брони и протезов (надетые, запасные у персонажей, на складах): значения шаблона, "
                 "множители и итоговые `calc_*`", columns, rows)


# --------------------------------------------------------------------------- инвентарь

def inventory_table(current: dict[str, Any], index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for c in current.get("characters") or []:
        items = c.get("items") or []
        by_item_sid = {it.get("item_sid"): it for it in items}
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for it in items:
            cont, sec = it.get("container") or "", it.get("section") or ""
            sid, holder_sid = it.get("base_data_sid"), None
            if cont.startswith("inventory>"):
                place = ("backpack", sec or "backpack_content")
                holder_sid = (by_item_sid.get(cont.rsplit(">", 1)[1]) or {}).get("base_data_sid")
            else:
                place = SECTION_PLACES.get(sec, ("unknown", sec))
            name = _label(index, sid)
            row = {
                "character_id": c.get("id"), "character_name": c.get("name"), "container": place[0], "slot": place[1],
                "backpack_name": _label(index, holder_sid) if holder_sid else None, "backpack_sid": holder_sid,
                "item_name": name, "item_type": _type_of(index, sid or ""), "base_sid": sid, "item_sid": it.get("item_sid"),
                "level": it.get("level"), "quality": it.get("quality"),
                "grade": _instance_grade(index, sid, it.get("level"), it.get("material_sid") or None),
                "quantity": it.get("quantity"), "stolen": True if it.get("is_stolen") else None,
                "charges": it.get("charges"), "unique": it.get("unique"), "unresolved": True if name is None else None,
            }
            groups[(c.get("id"), sid, it.get("level"), place[1])].append(row)
        for group in groups.values():
            group.sort(key=lambda r: (str(r["backpack_sid"]), str(cell(r["quality"])), str(cell(r["charges"])),
                                      str(r["stolen"]), str(r["quantity"]), str(r["item_sid"])))
            for n, row in enumerate(group, 1):
                row["dup_no"] = n
                rows.append(row)
    rows.sort(key=lambda r: (str(r["character_id"]), CONTAINER_ORDER.index(r["container"]), r["slot"], str(r["base_sid"]),
                             r["level"] if isinstance(r["level"], (int, float)) else -1, r["dup_no"]))
    columns = [
        ("character_id", "ID персонажа"), ("character_name", "Имя персонажа"),
        ("container", "body - надето (оружие, броня, надетый рюкзак); belt - пояс; backpack - внутри рюкзака; "
                      "inventory - основной инвентарь; unknown - раздел не распознан (значение в `slot`)"),
        ("slot", "weapon_back, weapon_hip, head, shirt, body, legs, boots, backpack (надетый рюкзак), belt; main - основной "
                 "инвентарь; backpack_content - содержимое рюкзака"),
        ("backpack_name", "Для container = backpack: название рюкзака, в котором лежит предмет"),
        ("backpack_sid", "Для container = backpack: base_sid рюкзака"),
        ("item_name", "Название предмета (игра, ru); пусто, если sid нет в данных игры"),
        ("item_type", "Тип записи: ITEM, WEAPON, CROSSBOW, ARMOUR, CONTAINER, LIMB_REPLACEMENT..."),
        ("base_sid", "sid записи предмета в данных игры; стабилен между сейвами"),
        ("item_sid", "sid экземпляра в сейве; уникален только внутри снимка, между сейвами меняется - для сравнения "
                     "снимков не использовать"),
        ("level", "Уровень экземпляра как в сейве (у расходников 0)"),
        ("quality", "quality экземпляра как в сейве (у брони и оружия 100, у прочих 1; смысл у расходников не установлен)"),
        ("grade", "Грейд: оружие - запись material, броня и арбалет - по уровню, протез - по уровню; пусто - не определён"),
        ("quantity", "Количество в стопке"), ("stolen", "true - предмет краденый; пусто - нет"),
        ("charges", "charges как в сейве (для еды - питательность на единицу)"), ("unique", "Признак unique из сейва"),
        ("unresolved", "true - base_sid не найден в данных игры"),
        ("dup_no", "Порядковый номер среди экземпляров с одинаковым (character_id, base_sid, level, slot): ключ сравнения "
                   "снимков - (character_id, base_sid, level, slot, dup_no)"),
    ]
    return Table("inventory.csv", "character_id, container, slot, base_sid, level, dup_no",
                 "Все предметы персонажей отряда поэкземплярно: надетое, пояс, рюкзаки, инвентарь", columns, rows)


# --------------------------------------------------------------------------- склады и цены

def _num_key(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else -1.0


def storage_table(current: dict[str, Any], locations: dict[str, Any] | None,
                  index: dict[str, list[Any]] | None, zone_places: dict[str, str] | None = None) -> Table:
    buildings = (current.get("property") or {}).get("buildings") or []
    raw, _nodes = storage_rows(buildings, locations, index, zone_places)
    rows = []
    for r in raw:
        rows.append({
            "settlement": r["settlement"],
            "location_key": r["settlement"] or ZONE_KEY_FORMAT.format(zone=r["zone"]),
            "settlement_status": r["settlement_status"], "zone": r["zone"],
            "building_id": r["building_id"], "building": r["building"], "container": r["container"],
            "item": r["item"], "item_sid": r["item_sid"], "quantity": r["quantity"],
            "base_price": r["base_price"], "base_price_source": r["base_price_source"],
            "unresolved": r["unresolved"],
            # Экземпляры разного уровня и качества остаются отдельными строками: сворачивать по base_sid нельзя.
            "level": r["level"], "quality": r["quality"],
            "grade": _instance_grade(index, r["base_sid"], r["level"], r["material_sid"]),
            "charges": r["charges"], "stolen": True if r["stolen"] else None, "unique": r["unique"],
        })
    rows.sort(key=lambda r: (*(str(r[k] or "") for k in
                               ("location_key", "zone", "building_id", "container", "item", "item_sid")),
                             _num_key(r["level"]), _num_key(r["quality"]), _num_key(r["charges"])))
    columns = [
        ("settlement", "Поселение; пусто, если не определено"),
        ("location_key", "Ключ места: поселение или «зона N (поселение не определено)»"),
        ("settlement_status", "Способ привязки: in_location, town, near_location, zone_location (единственное поселение зоны по locations и справочнику мира), zone"),
        ("zone", "Зона карты"), ("building_id", "ID здания в сохранении"), ("building", "Название здания"),
        ("container", "Контейнер внутри здания; пусто - без контейнера"),
        ("item", "Название предмета (игра, ru)"), ("item_sid", "sid предмета"), ("quantity", "Количество"),
        ("base_price", "Базовая цена `value` (последний файл в порядке загрузки модов)"),
        ("base_price_source", "Файл, задающий цену"),
        ("unresolved", "Предмет не найден в данных игры"),
        ("level", "Уровень экземпляра как в сейве (у расходников 0)"),
        ("quality", "quality экземпляра как в сейве (у брони и оружия 100, у прочих 1; смысл у расходников не установлен)"),
        ("grade", "Грейд: оружие - запись material, броня и арбалет - по уровню, протез - по уровню; пусто - не определён"),
        ("charges", "charges как в сейве (для еды - питательность на единицу)"),
        ("stolen", "true - краденый; пусто - нет"), ("unique", "Признак unique из сейва"),
    ]
    return Table("storage.csv", "location_key, zone, building_id, container, item, item_sid",
                 "Предметы в хранилищах зданий игрока: строка на запись сейва (стопка), экземпляры разного уровня раздельно", columns, rows)


def item_prices_table(index: dict[str, list[Any]] | None) -> Table:
    rows = []
    for sid, entry in (index or {}).items():
        stats = entry[4] if len(entry) > 4 and entry[4] else {}
        if "value" not in stats:
            continue
        history = stats.get("value_history")
        rows.append({
            "sid": sid, "name_en": entry[0], "name_ru": entry[1], "type": entry[2], "value": stats.get("value"),
            "value_1": stats.get("value 1"), "value_source": stats.get("value_source") or entry[3],
            "value_history": _join(f"{src}={cell(v)}" for src, v in history) if history else None,
        })
    rows.sort(key=lambda r: r["sid"])
    columns = [
        ("sid", "sid записи"), ("name_en", "Название (en)"), ("name_ru", "Название (ru)"),
        ("type", "Тип записи (ITEM, ARMOUR, WEAPON, CROSSBOW, CONTAINER, LIMB_REPLACEMENT...)"),
        ("value", "Базовая цена `value`: последнее определение в порядке загрузки"),
        ("value_1", "`value 1` - цена высшего качества (протезы, арбалеты)"),
        ("value_source", "Файл последнего определения цены"),
        ("value_history", "Определения по файлам в порядке загрузки `файл=значение`, если их несколько"),
    ]
    return Table("item_prices.csv", "sid", "Базовые цены записей игры без торговых коэффициентов", columns, rows)


def trade_modifiers_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    trade = (records or {}).get("trade") or {}
    rows: list[dict[str, Any]] = []

    def base(group: str, rec: dict[str, Any]) -> dict[str, Any]:
        return {"group": group, "record_sid": rec["sid"], "record_type": rec.get("type"),
                "record_name": rec.get("name"), "record_files": _join(rec.get("files") or [])}

    def add_refs(group: str, rec: dict[str, Any]) -> None:
        for cat, pairs in sorted((rec.get("refs") or {}).items()):
            for sid, v in pairs:
                v = list(v) + [None] * (3 - len(v))
                rows.append({**base(group, rec), "field": cat, "ref_sid": sid, "ref_name": _label(index, sid),
                             "ref_v0": v[0], "ref_v1": v[1], "ref_v2": v[2]})

    for rec in trade.get("cultures") or []:
        add_refs("culture", rec)
    for rec in trade.get("culture_links") or []:
        add_refs("culture_link", rec)
    for rec in trade.get("squad_templates") or []:
        for key in ("is trader", "buy mult", "sell mult", "vendors refresh time", "vendor money"):
            if key in rec:
                rows.append({**base("squad_template", rec), "field": key, "value": rec[key]})
        add_refs("squad_template", rec)
    for rec in trade.get("manufacturers") or []:
        if "price mod" in rec:
            rows.append({**base("manufacturer", rec), "field": "price mod", "value": rec["price mod"]})
    for rec in trade.get("constants") or []:
        for key, value in sorted((rec.get("values") or {}).items()):
            rows.append({**base("constant", rec), "field": key, "value": value})
    rows.sort(key=lambda r: tuple(str(r.get(k) or "") for k in ("group", "record_sid", "field", "ref_sid")))
    columns = [
        ("group", "culture (ITEMS_CULTURE, тип 104), culture_link (TOWN/FACTION: trade culture, trade prices), "
                  "squad_template (SQUAD_TEMPLATE, тип 52), manufacturer (WEAPON_MANUFACTURER, тип 51), "
                  "constant (CONSTANTS, тип 27)"),
        ("record_sid", "sid записи"), ("record_type", "Тип записи"), ("record_name", "Имя записи (en)"),
        ("record_files", "Файлы, определяющие запись, в порядке загрузки"),
        ("field", "Поле записи или категория ссылки"), ("value", "Значение скалярного поля"),
        ("ref_sid", "sid записи, на которую указывает ссылка"), ("ref_name", "Её название (ru или en)"),
        ("ref_v0", "Значение ссылки `v[0]`: для `trade prices` множитель цены, 100 = 100%"),
        ("ref_v1", "Значение ссылки `v[1]` (смысл не установлен)"),
        ("ref_v2", "Значение ссылки `v[2]` (смысл не установлен)"),
    ]
    return Table("trade_modifiers.csv", "group, record_sid, field, ref_sid",
                 "Сырые торговые записи игры без вычисленных цен продажи и покупки", columns, rows)


# --------------------------------------------------------------------------- состояние мира

def unique_npcs_table(current: dict[str, Any]) -> Table:
    rows = [{
        "sid": u.get("sid"), "name_en": u.get("name"), "name_ru": u.get("name_ru"),
        "state_code": u.get("state_code"), "player": u.get("player"), "captured_by": u.get("captured_by"),
    } for u in (current.get("world_state") or {}).get("unique_npcs") or []]
    rows.sort(key=lambda r: str(r["sid"]))
    columns = [
        ("sid", "sid уникального NPC (usedUniques)"), ("name_en", "Имя (en)"), ("name_ru", "Имя (ru)"),
        ("state_code", "Код состояния из сохранения: 0 - мёртв, 1 - жив и свободен, 2 - в плену"),
        ("player", "NPC входит в отряд игрока"),
        ("captured_by", "Персонаж отряда, который несёт пленного"),
    ]
    return Table("unique_npcs.csv", "sid", "Уникальные NPC мира и их состояние", columns, rows)


def unique_entities_table(
    unique_entities: dict[str, Any] | None,
    current: dict[str, Any] | None = None,
) -> Table:
    from tools.unique_entities_registry import resolve_primary_location

    columns = [
        ("entity_kind", "Категория сущности: npc, boss, meitou"),
        ("sid", "sid шаблона (npc_sid для персонажей и боссов, base_sid для оружия Мейто)"),
        ("name_ru", "Имя/название на русском языке"),
        ("name_en", "Имя/название на английском языке"),
        ("state_code", "Код состояния: 0 мёртв, 1 жив и свободен, 2 в плену"),
        ("squad_status", "Статус в отряде игрока: confirmed_in_squad, confirmed_not_in_squad, unresolved"),
        ("character_id", "Идентификатор персонажа игрока, если принят в отряд"),
        ("primary_location_instance_id", "Идентификатор основной локации (поселения/города)"),
        ("location_resolution", "Точность привязки к локации: unique, ambiguous, unresolved"),
        ("meitou_base_sid", "sid базовой модели оружия Мейто"),
        ("carrier_or_storage", "Носитель или хранилище экземпляра (вид:имя)"),
        ("provenance_status", "Статус происхождения: confirmed_trophy, unconfirmed, либо источник зачистки босса"),
    ]
    if unique_entities is None:
        rows: list[dict[str, Any]] = []
    else:
        npc_by_sid = {
            npc["npc_sid"]: npc
            for npc in (unique_entities.get("unique_npcs") or [])
            if npc.get("npc_sid")
        }
        rows = []
        for npc in unique_entities.get("unique_npcs") or []:
            dyn = npc.get("dynamic") or {}
            locs = npc.get("locations") or []
            primary_iid, resolution = resolve_primary_location(locs)
            rows.append({
                "entity_kind": "npc",
                "sid": npc.get("npc_sid") or npc.get("sid"),
                "name_ru": npc.get("name_ru"),
                "name_en": npc.get("name_en"),
                "state_code": dyn.get("state_code"),
                "squad_status": dyn.get("squad_status"),
                "character_id": dyn.get("character_id"),
                "primary_location_instance_id": primary_iid,
                "location_resolution": resolution,
                "meitou_base_sid": None,
                "carrier_or_storage": None,
                "provenance_status": None,
            })
        for boss in unique_entities.get("bosses") or []:
            sid = boss.get("npc_sid") or boss.get("sid")
            npc_entry = npc_by_sid.get(sid)
            obs = boss.get("observed_state") or boss.get("dynamic") or {}
            npc_dyn = (npc_entry.get("dynamic") if npc_entry else {}) or {}

            state_code = obs.get("state_code") if obs.get("state_code") is not None else npc_dyn.get("state_code")
            squad_status = obs.get("squad_status") or npc_dyn.get("squad_status")
            character_id = obs.get("character_id") or npc_dyn.get("character_id")

            locs = boss.get("static_locations") or boss.get("locations") or []
            primary_iid, resolution = resolve_primary_location(locs)

            cleared = boss.get("cleared_evidence")
            personal = boss.get("personal_evidence")
            source = cleared.get("source") if cleared else (personal.get("event_id") if personal else None)
            rows.append({
                "entity_kind": "boss",
                "sid": sid,
                "name_ru": boss.get("name_ru"),
                "name_en": boss.get("name_en"),
                "state_code": state_code,
                "squad_status": squad_status,
                "character_id": character_id,
                "primary_location_instance_id": primary_iid,
                "location_resolution": resolution,
                "meitou_base_sid": None,
                "carrier_or_storage": None,
                "provenance_status": source,
            })
        for weapon in unique_entities.get("meitou_weapons") or []:
            base_sid = weapon.get("base_sid") or weapon.get("sid")
            observed = weapon.get("observed_instances") or []
            carrier_or_storage = None
            if observed:
                inst = observed[0]
                storage_type = inst.get("storage_type") or inst.get("location_kind") or ""
                target_name = (
                    inst.get("carrier_name")
                    or inst.get("container_name")
                    or inst.get("storage_settlement")
                    or inst.get("storage_building")
                    or inst.get("storage_location_key")
                    or ""
                )
                carrier_or_storage = f"{storage_type}:{target_name}"
            rows.append({
                "entity_kind": "meitou",
                "sid": base_sid,
                "name_ru": weapon.get("name_ru"),
                "name_en": weapon.get("name_en"),
                "state_code": None,
                "squad_status": None,
                "character_id": None,
                "primary_location_instance_id": None,
                "location_resolution": None,
                "meitou_base_sid": base_sid,
                "carrier_or_storage": carrier_or_storage,
                "provenance_status": weapon.get("provenance_status"),
            })
        rows.sort(key=lambda r: (r["entity_kind"], str(r.get("sid") or ""), str(r.get("primary_location_instance_id") or "")))
    return Table("unique_entities.csv", "sid", "Реестр уникальных сущностей Kenshi (NPC, боссы, оружие Мейто)", columns, rows)


def town_overrides_table(current: dict[str, Any], index: dict[str, list[Any]] | None) -> Table:
    rows = [{
        "replacement_sid": t.get("replacement_sid"), "town": t.get("name"), "raw_name": t.get("raw_name"),
        "replacement_name": t.get("replacement_name"), "replacement_name_ru": t.get("replacement_name_ru"),
        "faction_sid": t.get("faction"), "faction_name": _label(index, t.get("faction")),
        "factions_here": _join(t.get("factions_here") or []),
    } for t in (current.get("world_state") or {}).get("town_overrides") or []]
    rows.sort(key=lambda r: (str(r["replacement_sid"]), str(r["town"])))
    columns = [
        ("replacement_sid", "sid записи-замены города (override)"), ("town", "Название города в сохранении"),
        ("raw_name", "Исходное имя записи состояния города"),
        ("replacement_name", "Название записи-замены (en), в нём вариант: destroyed, half destroyed..."),
        ("replacement_name_ru", "Название записи-замены (ru)"),
        ("faction_sid", "sid фракции города после замены"), ("faction_name", "Название фракции из данных игры"),
        ("factions_here", "sid фракций, присутствующих в городе, через `; `"),
    ]
    return Table("town_overrides.csv", "replacement_sid, town", "Города, заменённые записями override", columns, rows)


def diplomacy_table(current: dict[str, Any]) -> Table:
    rows = [{
        "faction_sid": r.get("faction_sid"), "faction_name": r.get("faction_name"),
        "relation": r.get("relation"), "trust": r.get("trust"), "trust_neg": r.get("trust_neg"),
    } for r in (current.get("diplomacy") or {}).get("relations") or []]
    rows.sort(key=lambda r: str(r["faction_sid"]))
    columns = [
        ("faction_sid", "sid фракции"), ("faction_name", "Название фракции"),
        ("relation", "Отношение фракции к игроку, сырое значение (-100...100)"),
        ("trust", "trust, сырое значение"), ("trust_neg", "trust_neg, сырое значение"),
    ]
    return Table("diplomacy.csv", "faction_sid", "Отношения фракций с игроком без классификации", columns, rows)


# --------------------------------------------------------------------------- условия реакций

def _refs(rec: dict[str, Any] | None, cat: str) -> list[tuple[str, list[Any]]]:
    return [(sid, v) for sid, v in ((rec or {}).get("refs") or {}).get(cat, [])]


def _root_towns(sid: str, parents: dict[str, set[str]], towns: dict[str, Any]) -> list[str]:
    """Города (TOWN с override town), из которых достижима запись override; цепочки вложенных override разворачиваются."""
    found: set[str] = set()
    seen: set[str] = set()
    stack = [sid]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for parent in parents.get(cur, ()):
            if parent in towns:
                found.add(parent)
            stack.append(parent)
    return sorted(found)


def reaction_rows(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> list[dict[str, Any]]:
    reactions = (records or {}).get("reactions") or {}
    overrides = {r["sid"]: r for r in reactions.get("town_overrides") or []}
    towns = {r["sid"]: r for r in reactions.get("town_parents") or []}
    states = {r["sid"]: r for r in reactions.get("world_states") or []}
    parents: dict[str, set[str]] = defaultdict(set)
    for rec in [*towns.values(), *overrides.values()]:
        for child, _v in _refs(rec, "override town"):
            parents[child].add(rec["sid"])

    rows: list[dict[str, Any]] = []
    for o_sid in sorted(overrides):
        o = overrides[o_sid]
        factions = _refs(o, "faction")
        for t_sid in _root_towns(o_sid, parents, towns) or [None]:
            for ws_sid, ws_v in _refs(o, "world state"):
                ws = states.get(ws_sid)
                subjects = [(cond, sid, v) for cond in REACTION_CONDITIONS for sid, v in _refs(ws, cond)] or [(None, None, [])]
                for cond, sid, v in subjects:
                    v = list(v) + [None] * (3 - len(v))
                    rows.append({
                        "override_sid": o_sid, "variant": o.get("name"),
                        "town_sid": t_sid, "town_name": (towns.get(t_sid) or {}).get("name"),
                        "town_name_ru": _names(index, t_sid)[1],
                        "world_state_sid": ws_sid, "world_state_name": (ws or {}).get("name"),
                        "world_state_v0": (list(ws_v) + [None])[0],
                        "condition": cond, "subject_sid": sid,
                        "subject_name": _label(index, sid), "subject_v0": v[0], "subject_v1": v[1], "subject_v2": v[2],
                        "faction_sid": _join(f for f, _ in factions),
                        "faction_name": _join(_label(index, f) for f, _ in factions),
                    })
    rows.sort(key=lambda r: tuple(str(r[k] or "") for k in
                                  ("town_sid", "override_sid", "world_state_sid", "condition", "subject_sid")))
    return rows


def world_reactions_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    columns = [
        ("override_sid", "sid записи override города"), ("variant", "Название варианта (запись override, en)"),
        ("town_sid", "sid города-родителя (TOWN с `override town`)"), ("town_name", "Название города (en)"),
        ("town_name_ru", "Название города (ru)"),
        ("world_state_sid", "sid записи World State, на которую ссылается override"),
        ("world_state_name", "Название World State"),
        ("world_state_v0", "Значение ссылки `world state` у override: по fcs.def 1 - состояние истинно, 0 - ложно; условия через AND"),
        ("condition", "Вид условия в World State: NPC is, NPC is NOT, player ally, player enemy; пусто - условий нет"),
        ("subject_sid", "sid NPC или фракции в условии"), ("subject_name", "Название субъекта (ru или en)"),
        ("subject_v0", "Для `NPC is`/`NPC is NOT` - код состояния NPC (0/1/2, см. unique_npcs.csv)"),
        ("subject_v1", "`v[1]` ссылки (смысл не установлен)"), ("subject_v2", "`v[2]` ссылки (смысл не установлен)"),
        ("faction_sid", "sid итоговой фракции override (`faction`)"), ("faction_name", "Название фракции"),
    ]
    return Table("world_reactions.csv", "town_sid, override_sid, world_state_sid, condition, subject_sid",
                 "Условия вариантов городов: одна строка на условие World State", columns,
                 reaction_rows(records, index))


def world_reaction_links_table(current: dict[str, Any], records: dict[str, Any] | None,
                               index: dict[str, list[Any]] | None) -> Table:
    npcs = {u.get("sid"): u for u in (current.get("world_state") or {}).get("unique_npcs") or []}
    rows = []
    for r in reaction_rows(records, index):
        if r["condition"] not in ("NPC is", "NPC is NOT"):
            continue
        npc = npcs.get(r["subject_sid"])
        rows.append({
            "override_sid": r["override_sid"], "town_sid": r["town_sid"], "world_state_sid": r["world_state_sid"],
            "condition": r["condition"], "npc_sid": r["subject_sid"], "npc_name": r["subject_name"],
            "required_state_code": r["subject_v0"], "npc_in_save": npc is not None,
            "current_state_code": npc.get("state_code") if npc else None,
            "current_player": npc.get("player") if npc else None,
            "current_captured_by": npc.get("captured_by") if npc else None,
        })
    columns = [
        ("override_sid", "sid записи override"), ("town_sid", "sid города-родителя"),
        ("world_state_sid", "sid World State"), ("condition", "NPC is или NPC is NOT"),
        ("npc_sid", "sid уникального NPC"), ("npc_name", "Название NPC"),
        ("required_state_code", "Код состояния в условии (`v[0]` ссылки)"),
        ("npc_in_save", "NPC присутствует среди usedUniques сохранения"),
        ("current_state_code", "Текущий state_code из сохранения"), ("current_player", "NPC в отряде игрока"),
        ("current_captured_by", "Кто несёт пленного"),
    ]
    return Table("world_reaction_links.csv", "town_sid, override_sid, world_state_sid, condition, npc_sid",
                 "Условия `NPC is`/`NPC is NOT` с текущим state_code NPC; соответствие условия не вычисляется",
                 columns, rows)


# --------------------------------------------------------------------------- справочник юнитов

# Поля записи STATS (fcs.def): ключ хранения -> подпись (диапазон 0-100). Имена колонок: stat_<ключ>.
STAT_FIELDS: tuple[tuple[str, str], ...] = (
    ("dexterity", "Ловкость"), ("strength", "Сила"), ("toughness2", "Крепость"), ("perception", "Восприятие"),
    ("attack", "Атака (ближний бой)"), ("defence", "Защита (ближний бой)"), ("dodge", "Уворот"),
    ("unarmed", "Боевые искусства"), ("athletics", "Атлетика"), ("swimming", "Плавание"),
    ("engineer", "Инженерия"), ("medic", "Полевой медик"), ("robotics", "Роботехника"), ("science", "Наука"),
    ("assassin", "Убийство"), ("lockpicking", "Взлом"), ("stealth", "Скрытность"), ("thievery", "Воровство"),
    ("armour smith", "Ковка доспехов"), ("bow smith", "Ковка арбалетов"), ("cooking", "Готовка"),
    ("farming", "Фермерство"), ("labouring", "Работа"), ("weapon smith", "Ковка оружия"),
    ("blunt", "Дробящее"), ("bow", "Арбалеты"), ("ff", "Точная стрельба"), ("hackers", "Рубачи"),
    ("heavy weapons", "Тяжелое оружие"), ("katana", "Катаны"), ("poles", "Древковое"), ("sabres", "Сабли"),
    ("turrets", "Турели"),
)
# Ссылки шаблона персонажа, публикуемые в unit_template_gear.csv: категория -> смысл v[0..2] (fcs.def, CHARACTER).
GEAR_CATEGORIES: dict[str, str] = {
    "weapons": "v0 количество, v1 слот (0 пояс, 1 спина), v2 абсолютный шанс, % (сумма < 100 - шанс остаться без оружия)",
    "crossbows": "v0 количество, v1 шанс, % (шансы не относительные)",
    "weapon level": "v0 относительный шанс; запись - производитель оружия (уровень оружия)",
    "clothing": "v0 количество, v1 шанс, % (нормируется среди предметов одного типа)",
    "backpack": "v1 абсолютный шанс, %",
    "robot limbs": "v0 качество, v1 относительный шанс; отрицательное качество - оставить настоящую конечность",
    "weapon": "встроенное оружие животного",
}
# Роли в составе отряда (SQUAD_TEMPLATE): смысл v[0..2] по fcs.def.
SQUAD_ROLES: dict[str, str] = {
    "leader": "v0 число лидеров", "squad": "v0 минимум, v1 максимум (0 - не случайно)",
    "squad2": "v0 минимум, v1 максимум (0 - не случайно); вторая половина отряда",
    "animals": "v0 минимум, v1 максимум, v2 возраст (100 взрослый, 0 детёныш)",
    "animals2": "v0 минимум, v1 максимум, v2 возраст (100 взрослый, 0 детёныш)",
    "choosefrom list": "v0 шанс; число выбираемых - num_random_chars и num_random_chars_max в squad_templates.csv",
    "prisoners": "шаблоны отрядов-заключённых", "slaves": "шаблоны отрядов-рабов",
    "housemates": "v0 минимум, v1 максимум дополнительных отрядов в том же здании",
}
FACTION_LINKS: dict[str, str] = {"relations": "relation", "coexistence": "coexistence", "legal system": "legal_system"}


def _units(records: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    return (records or {}).get("units") or {}


def _ref_sids(rec: dict[str, Any], cat: str) -> list[str]:
    return [sid for sid, _v in _refs(rec, cat)]


def _triple(v: Sequence[Any]) -> list[Any]:
    return list(v) + [None] * (3 - len(v))


def _type_of(index: dict[str, list[Any]] | None, sid: str) -> str | None:
    entry = (index or {}).get(sid)
    return entry[2] if entry else None


def _race_cells(index: dict[str, list[Any]] | None, rec: dict[str, Any]) -> dict[str, Any]:
    races = _refs(rec, "race") or _refs(rec, "races")
    return {"race_sids": _join(sid for sid, _v in races),
            "race_names": _join(_label(index, sid) for sid, _v in races),
            "race_weights": _join(v[0] for _sid, v in races)}


def unit_templates_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    units = _units(records)
    stats = {r["sid"]: r.get("float") or {} for r in units.get("stats") or []}
    rows: list[dict[str, Any]] = []
    for rec in units.get("characters") or []:
        ints, bools = rec.get("int") or {}, rec.get("bool") or {}
        factions = _ref_sids(rec, "faction")
        stats_sids = _ref_sids(rec, "stats")
        row: dict[str, Any] = {
            "sid": rec["sid"], "kind": "animal" if rec.get("type") == "ANIMAL_CHARACTER" else "character",
            "name_en": rec.get("name") or None, "name_ru": _names(index, rec["sid"])[1],
            "files": _join(rec.get("files") or []),
            "faction_sids": _join(factions), "faction_names": _join(_label(index, f) for f in factions),
            **_race_cells(index, rec),
            "female_chance": ints.get("female chance"), "unique": bools.get("unique"), "named": bools.get("named"),
            "shaved": bools.get("shaved"), "wears_uniform": bools.get("wears uniform"),
            "is_trader": bools.get("is trader"), "slave": ints.get("slave"), "npc_class": ints.get("NPC class"),
            "armour_grade": ints.get("armour grade"), "armour_upgrade_chance": ints.get("armour upgrade chance"),
            "stats_randomise": ints.get("stats randomise"), "combat_stats": ints.get("combat stats"),
            "ranged_stats": ints.get("ranged stats"), "stealth_stats": ints.get("stealth stats"),
            "strength": ints.get("strength"), "unarmed_stats": ints.get("unarmed stats"),
            "animal_strength": ints.get("animal strength"),
            "money_min": ints.get("money min"), "money_max": ints.get("money max"), "wages": ints.get("wages"),
            "bounty_chance": ints.get("bounty chance"), "bounty_amount": ints.get("bounty amount"),
            "bounty_amount_fuzz": ints.get("bounty amount fuzz"),
            "stats_sid": _join(stats_sids),
        }
        # Значения STATS подставляются только при единственной ссылке: при нескольких выбор записи игрой не установлен.
        values = stats.get(stats_sids[0], {}) if len(stats_sids) == 1 else {}
        for key, _ru in STAT_FIELDS:
            row["stat_" + key.replace(" ", "_")] = values.get(key)
        rows.append(row)
    rows.sort(key=lambda r: r["sid"])
    columns = [
        ("sid", "sid шаблона (CHARACTER или ANIMAL_CHARACTER)"), ("kind", "character или animal"),
        ("name_en", "Имя записи (en)"), ("name_ru", "Имя записи (ru, gamedata.po)"),
        ("files", "Файлы, определяющие запись, в порядке загрузки"),
        ("faction_sids", "sid фракции шаблона (`faction`); пусто - не задана (фракцию задаёт отряд, см. squad_templates.csv)"),
        ("faction_names", "Названия фракций"),
        ("race_sids", "sid рас (`race`), через `; `"), ("race_names", "Названия рас"),
        ("race_weights", "Шанс расы `v[0]`, % (по порядку race_sids)"),
        ("female_chance", "Шанс женского пола, % (0-100)"), ("unique", "Персонаж появляется один раз"),
        ("named", "Имя выбирается случайно из списка имён"), ("shaved", "Бритая голова (как у рабов)"),
        ("wears_uniform", "Одежда помечена как форма фракции"), ("is_trader", "Торговец"),
        ("slave", "Начальное состояние раба, код `slave` как хранится (0 - не раб)"),
        ("npc_class", "Класс NPC, код `NPC class` как хранится (перечисление CharacterTypeEnum, имена в данных не заданы)"),
        ("armour_grade", "Качество брони, код `armour grade` как хранится (перечисление ArmourRarity, имена в данных не заданы)"),
        ("armour_upgrade_chance", "Шанс брони на уровень выше обычного, % (0-100)"),
        ("stats_randomise", "Случайный разброс характеристик: +/- это число к значениям шаблона"),
        ("combat_stats", "`combat stats`: боевые навыки, если запись STATS не задана (0 - не задано)"),
        ("ranged_stats", "`ranged stats`: навыки дальнего боя, если запись STATS не задана"),
        ("stealth_stats", "`stealth stats`: навыки скрытности, если запись STATS не задана"),
        ("strength", "`strength`: сила, если запись STATS не задана"),
        ("unarmed_stats", "`unarmed stats`: боевые искусства и уворот, если запись STATS не задана"),
        ("animal_strength", "Фиксированная сила животного (`animal strength`)"),
        ("money_min", "Минимум денег"), ("money_max", "Максимум денег"), ("wages", "Дневное жалованье"),
        ("bounty_chance", "Шанс награды за голову, %"), ("bounty_amount", "Сумма награды"),
        ("bounty_amount_fuzz", "Разброс суммы награды +/-"),
        ("stats_sid", "sid записи STATS шаблона; пусто - не задана; stat_* заполнены, только если ссылка одна"),
    ]
    columns += [("stat_" + k.replace(" ", "_"), f"Запись STATS, `{k}`: {ru} [0-100]") for k, ru in STAT_FIELDS]
    return Table("unit_templates.csv", "sid", "Шаблоны персонажей и животных: раса, уровни и навыки как хранятся",
                 columns, rows)


def unit_template_gear_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for rec in _units(records).get("characters") or []:
        for cat in GEAR_CATEGORIES:
            for sid, v in _refs(rec, cat):
                v = _triple(v)
                rows.append({"template_sid": rec["sid"], "template_name": _label(index, rec["sid"]) or rec.get("name"),
                             "category": cat, "ref_sid": sid, "ref_name": _label(index, sid),
                             "ref_type": _type_of(index, sid), "v0": v[0], "v1": v[1], "v2": v[2]})
    rows.sort(key=lambda r: (r["template_sid"], r["category"], r["ref_sid"]))
    columns = [
        ("template_sid", "sid шаблона (unit_templates.csv)"), ("template_name", "Название шаблона (ru или en)"),
        ("category", "Категория ссылки: " + "; ".join(f"{k} - {v}" for k, v in GEAR_CATEGORIES.items())),
        ("ref_sid", "sid предмета или производителя"), ("ref_name", "Название (ru или en)"),
        ("ref_type", "Тип записи (WEAPON, CROSSBOW, ARMOUR, WEAPON_MANUFACTURER, CONTAINER, LIMB_REPLACEMENT)"),
        ("v0", "Значение ссылки `v[0]`"), ("v1", "Значение ссылки `v[1]`"), ("v2", "Значение ссылки `v[2]`"),
    ]
    return Table("unit_template_gear.csv", "template_sid, category, ref_sid",
                 "Оружие, арбалеты, уровень оружия, одежда и рюкзаки шаблонов персонажей", columns, rows)


def squad_templates_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for rec in _units(records).get("squads") or []:
        ints, bools = rec.get("int") or {}, rec.get("bool") or {}
        factions, states, override = _ref_sids(rec, "faction"), _refs(rec, "world state"), _ref_sids(rec, "race override")
        rows.append({
            "sid": rec["sid"], "name_en": rec.get("name") or None, "name_ru": _names(index, rec["sid"])[1],
            "files": _join(rec.get("files") or []),
            "faction_sids": _join(factions), "faction_names": _join(_label(index, f) for f in factions),
            "world_state_sids": _join(sid for sid, _v in states),
            "world_state_required": _join(v[0] for _sid, v in states),
            "race_override_sids": _join(override), "race_override_names": _join(_label(index, r) for r in override),
            "roaming_military": bools.get("roaming military"),
            "patrol_approaches_towns": bools.get("patrol approaches towns"),
            "dont_multiply": bools.get("dont multiply"), "regenerates": bools.get("regenerates"),
            "is_trader": bools.get("is trader"),
            "num_random_chars": ints.get("num random chars"), "num_random_chars_max": ints.get("num random chars max"),
        })
    rows.sort(key=lambda r: r["sid"])
    columns = [
        ("sid", "sid шаблона отряда (SQUAD_TEMPLATE, тип 52)"), ("name_en", "Имя записи (en)"),
        ("name_ru", "Имя записи (ru, gamedata.po)"), ("files", "Файлы, определяющие запись, в порядке загрузки"),
        ("faction_sids", "sid фракции отряда (`faction`); пусто - не задана"), ("faction_names", "Названия фракций"),
        ("world_state_sids", "sid World State (`world state`): отряд появляется, только когда условия выполнены (AND)"),
        ("world_state_required", "Требуемое значение `v[0]` по порядку world_state_sids: 1 - состояние истинно, 0 - ложно"),
        ("race_override_sids", "sid расы, принудительной для всего отряда (`race override`)"),
        ("race_override_names", "Названия рас"),
        ("roaming_military", "Считается бродячими войсками, даже если есть родной город"),
        ("patrol_approaches_towns", "Патруль заходит в ближние города"),
        ("dont_multiply", "Размер не зависит от множителя размера отрядов в настройках"),
        ("regenerates", "Отряд-житель медленно восстанавливает потери"), ("is_trader", "Торговый отряд"),
        ("num_random_chars", "Минимум персонажей, выбираемых из `choosefrom list`"),
        ("num_random_chars_max", "Максимум персонажей, выбираемых из `choosefrom list`"),
    ]
    return Table("squad_templates.csv", "sid", "Шаблоны отрядов и патрулей: фракция, условия появления, флаги",
                 columns, rows)


def squad_members_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for rec in _units(records).get("squads") or []:
        for role in SQUAD_ROLES:
            for sid, v in _refs(rec, role):
                v = _triple(v)
                rows.append({"squad_sid": rec["sid"], "squad_name": _label(index, rec["sid"]) or rec.get("name"),
                             "role": role, "member_sid": sid, "member_name": _label(index, sid),
                             "member_type": _type_of(index, sid), "v0": v[0], "v1": v[1], "v2": v[2]})
    rows.sort(key=lambda r: (r["squad_sid"], r["role"], r["member_sid"]))
    columns = [
        ("squad_sid", "sid шаблона отряда (squad_templates.csv)"), ("squad_name", "Название отряда (ru или en)"),
        ("role", "Роль ссылки: " + "; ".join(f"{k} - {v}" for k, v in SQUAD_ROLES.items())),
        ("member_sid", "sid шаблона персонажа (CHARACTER), животного (ANIMAL_CHARACTER) или отряда (SQUAD_TEMPLATE)"),
        ("member_name", "Название (ru или en)"), ("member_type", "Тип записи"),
        ("v0", "Значение ссылки `v[0]` (смысл зависит от роли)"), ("v1", "Значение ссылки `v[1]`"),
        ("v2", "Значение ссылки `v[2]`"),
    ]
    return Table("squad_members.csv", "squad_sid, role, member_sid", "Состав шаблонов отрядов по ролям", columns, rows)


def squad_spawns_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    units = _units(records)
    rows: list[dict[str, Any]] = []
    for source, key, cat in (("biome_homeless", "biomes", "homeless spawns"), ("town_roaming", "towns", "roaming squads")):
        for place in units.get(key) or []:
            for sid, v in _refs(place, cat):
                rows.append({
                    "source": source, "place_sid": place["sid"], "place_name_en": place.get("name") or None,
                    "place_name_ru": _names(index, place["sid"])[1],
                    "place_faction_sid": _join(f for f, _v in _refs(place, "faction")),
                    "spawn_amount": place.get("homeless spawn amount"),
                    "squad_sid": sid, "squad_name": _label(index, sid), "ref_v0": _triple(v)[0],
                })
    rows.sort(key=lambda r: (r["source"], r["place_sid"], r["squad_sid"]))
    columns = [
        ("source", "biome_homeless - `homeless spawns` группы биомов (BIOME_GROUP); town_roaming - `roaming squads` города (TOWN)"),
        ("place_sid", "sid группы биомов или города"), ("place_name_en", "Название (en)"),
        ("place_name_ru", "Название (ru, gamedata.po)"),
        ("place_faction_sid", "sid фракции города (`faction`); для биомов пусто"),
        ("spawn_amount", "`homeless spawn amount` биома: примерное число бездомных отрядов на зону; для городов пусто"),
        ("squad_sid", "sid шаблона отряда (squad_templates.csv)"), ("squad_name", "Название отряда (ru или en)"),
        ("ref_v0", "Значение ссылки `v[0]`: у biome_homeless - относительный шанс появления; у town_roaming смысл не задан"),
    ]
    return Table("squad_spawns.csv", "source, place_sid, squad_sid",
                 "Где появляются отряды: бездомные отряды групп биомов и бродячие отряды городов; привязки группы биомов "
                 "к зонам карты в данных нет", columns, rows)


def faction_relations_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for rec in _units(records).get("factions") or []:
        for cat, kind in FACTION_LINKS.items():
            for sid, v in _refs(rec, cat):
                rows.append({"faction_sid": rec["sid"], "faction_name": _label(index, rec["sid"]) or rec.get("name"),
                             "kind": kind, "other_sid": sid, "other_name": _label(index, sid),
                             "value": _triple(v)[0] if kind == "relation" else None})
    rows.sort(key=lambda r: (r["faction_sid"], r["kind"], r["other_sid"]))
    columns = [
        ("faction_sid", "sid фракции-источника"), ("faction_name", "Название (ru или en)"),
        ("kind", "relation - `relations` (отношение к другой фракции); coexistence - `coexistence` (фракции, с которыми "
                 "стремится сосуществовать); legal_system - `legal system` (чьи законы применяются)"),
        ("other_sid", "sid другой фракции"), ("other_name", "Название (ru или en)"),
        ("value", "Только для relation: отношение -100...100 как хранится; не указанные пары равны `default_relation` "
                  "фракции (faction_hostility.csv); симметрия не предполагается"),
    ]
    return Table("faction_relations.csv", "faction_sid, kind, other_sid",
                 "Заданные в данных отношения фракций между собой без классификации", columns, rows)


def faction_hostility_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    for rec in _units(records).get("factions") or []:
        ints, floats, bools = rec.get("int") or {}, rec.get("float") or {}, rec.get("bool") or {}
        rows.append({
            "faction_sid": rec["sid"], "faction_name_en": rec.get("name") or None,
            "faction_name_ru": _names(index, rec["sid"])[1], "files": _join(rec.get("files") or []),
            "default_relation": ints.get("default relation"), "enemy_classification": ints.get("enemy classification"),
            "business_relations": ints.get("business relations"), "trustworthy": floats.get("trustworthy"),
            "effect_of_anger": floats.get("effect of anger"), "effect_of_happy": floats.get("effect of happy"),
            "emotion_fade_rate": floats.get("emotion fade rate"),
            "run_away_ratio_of_squad_size": floats.get("run away ratio of squad size"),
            "run_away_ratio_relative_to_enemy": floats.get("run away ratio relative to enemy"),
            "not_real": bools.get("not real"), "anti_slavery": bools.get("anti slavery"),
            "heals_strangers": bools.get("heals strangers"), "offers_bounties": bools.get("offers bounties"),
            "allow_slaves_weapons": bools.get("allow slaves weapons"),
            "fundamental_type": ints.get("fundamental type"), "squad_formation": ints.get("squad formation"),
            **_race_cells(index, rec),
        })
    rows.sort(key=lambda r: r["faction_sid"])
    columns = [
        ("faction_sid", "sid фракции"), ("faction_name_en", "Имя записи (en)"), ("faction_name_ru", "Имя записи (ru)"),
        ("files", "Файлы, определяющие запись, в порядке загрузки"),
        ("default_relation", "Отношение к фракциям, не перечисленным в `relations`, -100...100"),
        ("enemy_classification", "Максимальное отношение, при котором фракция считает другую врагом (нападает при виде)"),
        ("business_relations", "Минимальное отношение для торговли"),
        ("trustworthy", "Надёжность фракции -100...100"),
        ("effect_of_anger", "Влияние гнева на реальные отношения (множитель)"),
        ("effect_of_happy", "Влияние довольства на реальные отношения (множитель)"),
        ("emotion_fade_rate", "Скорость возврата эмоций к нулю (множитель)"),
        ("run_away_ratio_of_squad_size", "Доля потерь отряда, после которой он бежит (0-1)"),
        ("run_away_ratio_relative_to_enemy", "Соотношение сил с врагом, при котором отряд бежит"),
        ("not_real", "Не человеческая фракция: действия против неё не считаются преступлением"),
        ("anti_slavery", "Против рабства"), ("heals_strangers", "Помогает раненым чужакам"),
        ("offers_bounties", "Может назначать награду за голову"), ("allow_slaves_weapons", "Рабам разрешено оружие"),
        ("fundamental_type", "Базовая классификация, код `fundamental type` (CharacterTypeEnum, имена в данных не заданы)"),
        ("squad_formation", "Построение отрядов, код `squad formation` (0 RANDOM, 1 CARAVAN, 2 MILITARY: fcs_enums.def)"),
        ("race_sids", "sid рас фракции (`races`), через `; `"), ("race_names", "Названия рас"),
        ("race_weights", "Доля расы `v[0]` (относительная) по порядку race_sids"),
    ]
    return Table("faction_hostility.csv", "faction_sid",
                 "Настройки фракций (враждебность, доверие, бегство) и расовый состав; враждебности по расе, полу и "
                 "протезам в данных нет", columns, rows)


# --------------------------------------------------------------------------- расы

# Коды WeatherAffecting: перечисление в fcs.def/fcs_enums.def не задано; 1-4 сверены с карточками брони (itemstats.WEATHER).
WEATHER_RU: dict[int, str] = {1: "песчаная буря", 2: "кислотный дождь", 3: "жара", 4: "газ"}
WEATHER_PREFIX: str = "weather immunity"
# Поле записи RACE -> (колонка, секция сырых данных): значения как хранятся; отсутствие поля - пустая ячейка.
RACE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("playable", "playable", "bool"), ("is robot", "is_robot", "bool"), ("single gender", "single_gender", "bool"),
    ("no hats", "no_hats", "bool"), ("no shirts", "no_shirts", "bool"), ("no shoes", "no_shoes", "bool"),
    ("speed min skill", "speed_min_skill", "int"), ("speed max skill", "speed_max_skill", "int"),
    ("walk speed", "walk_speed", "float"), ("combat move speed mult", "combat_move_speed_mult", "float"),
    ("swim speed mult", "swim_speed_mult", "float"), ("swim speed", "swim_speed", "int"),
    ("strength", "strength_mult", "float"), ("dexterity", "dexterity_mult", "float"),
    ("bleed rate", "bleed_rate", "float"), ("heal rate", "heal_rate", "float"), ("hunger rate", "hunger_rate", "float"),
    ("min blood", "min_blood", "float"), ("max blood", "max_blood", "float"),
    ("extra attack slots", "extra_attack_slots", "int"),
)
RACE_FIELD_NOTES: dict[str, str] = {
    "playable": "Игрок может выбрать расу", "is_robot": "Робот: атрибуты фиксированы и растут только протезами",
    "single_gender": "Один пол", "no_hats": "Нет слота шлема: шлемы не надеваются",
    "no_shirts": "Нет слота рубахи: рубахи не надеваются", "no_shoes": "Нет слота обуви: обувь не надевается",
    "speed_min_skill": "Скорость бега при минимальной Атлетике", "speed_max_skill": "Скорость бега при максимальной Атлетике",
    "walk_speed": "Скорость шага", "combat_move_speed_mult": "Множитель боевой скорости (fcs.def: 1.0 - человек)",
    "swim_speed_mult": "Множитель скорости плавания", "swim_speed": "Скорость плавания, целое как хранится",
    "strength_mult": "Множитель атрибута Сила", "dexterity_mult": "Множитель атрибута Ловкость",
    "bleed_rate": "Множитель кровотечения (0 - без крови)", "heal_rate": "Множитель лечения",
    "hunger_rate": "Скорость голода (множитель)", "min_blood": "Кровь при минимальной силе/возрасте",
    "max_blood": "Кровь при максимальной силе/возрасте", "extra_attack_slots": "Добавка к числу атакующих слотов",
}
RACE_PUBLISHED_KEYS: frozenset[str] = frozenset({f for f, _c, _s in RACE_FIELDS} | {"natural armour", "combat anatomy"})


def races_table(records: dict[str, Any] | None, index: dict[str, list[Any]] | None) -> Table:
    races = (records or {}).get("races") or []
    # Ограничения брони: у ARMOUR поле `races` (только эти расы) и `races exclude` (кроме этих рас).
    only: dict[str, list[str]] = defaultdict(list)
    excluded: dict[str, list[str]] = defaultdict(list)
    whitelisted = 0
    for sid, entry in (index or {}).items():
        if entry[2] != "ARMOUR" or len(entry) < 5 or not entry[4]:
            continue
        stats = entry[4]
        whitelisted += 1 if stats.get("refs:races") else 0
        for race in stats.get("refs:races") or {}:
            only[race].append(_label(index, sid) or sid)
        for race in stats.get("refs:races exclude") or {}:
            excluded[race].append(_label(index, sid) or sid)
    rows: list[dict[str, Any]] = []
    for rec in races:
        sid, files = rec["sid"], rec.get("files") or []
        by_file = rec.get("weather_by_file") or {}
        ints = rec.get("int") or {}
        codes = [ints[k] for k in sorted((k for k in ints if k.startswith(WEATHER_PREFIX)),
                                         key=lambda k: int(k[len(WEATHER_PREFIX):] or 0))]
        decoded = [WEATHER_RU.get(c, f"код {c} (не установлен)") for c in codes if c != 0]
        armour = (rec.get("refs") or {}).get("natural armour") or []
        anatomy = (rec.get("refs") or {}).get("combat anatomy") or []
        field_files = rec.get("field_files") or {}
        history = "; ".join(f"{f}={'/'.join(str(c) for c in v)}" for f, v in by_file.items()) if len(by_file) > 1 else None
        row: dict[str, Any] = {
            "sid": sid, "name_en": rec.get("name") or None, "name_ru": _names(index, sid)[1], "files": _join(files),
            "weather_immunity_codes": _join(codes),
            "weather_immunity": (_join(decoded) or "нет") if codes else None,
            "weather_immunity_source": field_files.get(WEATHER_PREFIX + "0"),
            "weather_immunity_by_file": history,
            "natural_armour_sids": _join(a for a, _v in armour),
            "natural_armour_names": _join(_label(index, a) for a, _v in armour),
            "natural_armour_template": _join(f"{k}={cell(v)}" for a, _v in armour
                                             for k, v in sorted(_stats(index, a).items()) if not k.startswith("refs:")),
            "combat_anatomy": _join(f"{_label(index, p) or p}={cell(v[0])}/{cell(v[1])}" for p, v in anatomy),
            "armour_only_for_race": _join(sorted(only.get(sid, []))),
            "armour_excluded_for_race": _join(sorted(excluded.get(sid, []))),
        }
        for field, col, section in RACE_FIELDS:
            row[col] = (rec.get(section) or {}).get(field)
        overridden = [k for k, f in field_files.items()
                      if files and f != files[0] and (k in RACE_PUBLISHED_KEYS or k.startswith(WEATHER_PREFIX))]
        row["overridden_fields"] = _join(f"{k}={field_files[k]}" for k in sorted(overridden))
        rows.append(row)
    rows.sort(key=lambda r: r["sid"])
    columns = [
        ("sid", "sid записи RACE (тип 7)"), ("name_en", "Имя записи (en)"), ("name_ru", "Имя записи (ru, gamedata.po)"),
        ("files", "Файлы, определяющие запись, в порядке загрузки: поле последнего файла заменяет предыдущие (rebirth после базы)"),
        ("weather_immunity_codes", "Коды `weather immunity0..N` как хранятся, через `; `; 0 - WA_NONE (иммунитета нет); пусто - полей нет"),
        ("weather_immunity", "Расшифровка: 1 песчаная буря, 2 кислотный дождь, 3 жара, 4 газ (перечисление WeatherAffecting "
                             "в fcs.def не задано, коды сверены с карточками брони); прочие коды помечены «не установлен»"),
        ("weather_immunity_source", "Файл последнего определения кодов"),
        ("weather_immunity_by_file", "Коды по файлам `файл=коды`, если запись определена в нескольких файлах; слияние идёт по "
                                     "ключам weather immunityN, удаление кода перекрывающим файлом не распознаётся"),
        ("natural_armour_sids", "sid ARMOUR естественной брони (`natural armour`): добавляется к надетой; уровень в записи расы не задан"),
        ("natural_armour_names", "Название"), ("natural_armour_template", "Поля шаблона этой брони `поле=значение` как хранятся"),
        ("combat_anatomy", "Части тела `часть=доля попадания/здоровье` как хранятся (val0/val1 ссылки LOCATIONAL_DAMAGE)"),
        ("armour_only_for_race", "Броня, у которой в `races` указана эта раса. Броня с непустым `races`, где расы нет, "
                                 f"для неё недоступна (таких записей брони в данных: {whitelisted})"),
        ("armour_excluded_for_race", "Броня, у которой эта раса указана в `races exclude`"),
    ]
    columns += [(col, f"`{field}`: {RACE_FIELD_NOTES[col]}; пусто - поле не хранится (значение по умолчанию fcs.def)")
                for field, col, _section in RACE_FIELDS]
    columns.append(("overridden_fields", "Поля, чьё значение задано не первым файлом записи: `поле=файл последнего определения`"))
    return Table("races.csv", "sid", "Расы игры (RACE) с учётом порядка загрузки модов: иммунитет к погоде, естественная броня, "
                 "скорости, множители, анатомия", columns, rows)


# --------------------------------------------------------------------------- постройки, исследования и ресурсы под форпост

DEFAULT_BUILD_MAT_SID: str = "580-gamedata.base"
RESEARCH_CRAFT_REFS: tuple[str, ...] = (
    "enable item", "enable weapon type", "enable weapon model", "enable armour",
    "enable crossbow", "enable robotics", "enable backpack",
)
BUILDING_CRAFT_REFS: tuple[str, ...] = (
    "item crafts", "weapon crafts", "armour crafts", "crossbow crafts", "robotics crafts", "backpack crafts",
)


def _production(records: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    return (records or {}).get("production") or {}


def _building_cost_slots(tpl_sid: str | None, b_rec: dict[str, Any] | None,
                         index: dict[str, list[Any]] | None) -> list[tuple[str, str, int]]:
    """Смета постройки по порядку слотов: [(item_sid, item_name, required_count)]."""
    stats = _stats(index, tpl_sid)
    order = (b_rec or {}).get("construction_order")
    if not order and stats.get("refs:construction"):
        order = [[k, v] for k, v in stats["refs:construction"].items()]
    if order:
        return [
            (str(s), _label(index, str(s)) or str(s), int(v[0]) if isinstance(v, list) and v else 1)
            for s, v in order
        ]
    b_ints = (b_rec or {}).get("int") or {}
    b_bools = (b_rec or {}).get("bool") or {}
    is_node = b_bools.get("is node") if "is node" in b_bools else stats.get("is node")
    bm = b_ints.get("build materials") if "build materials" in b_ints else stats.get("build materials")
    if not is_node and isinstance(bm, int) and bm > 0:
        return [(DEFAULT_BUILD_MAT_SID, _label(index, DEFAULT_BUILD_MAT_SID) or "Стройматериалы", bm)]
    return []


def buildings_table(current: dict[str, Any], locations: dict[str, Any] | None,
                    index: dict[str, list[Any]] | None, records: dict[str, Any] | None = None,
                    zone_places: dict[str, str] | None = None) -> Table:
    buildings = (current.get("property") or {}).get("buildings") or []
    places = _building_places(locations)
    b_records = {r["sid"]: r for r in _production(records).get("buildings") or []}

    # Карта handle здания -> назначенные работники из jobs персонажей
    workers_by_handle: dict[tuple[int | None, ...], list[str]] = defaultdict(list)
    for ch in current.get("characters") or []:
        c_name = ch.get("name") or ch.get("id") or "Персонаж"
        jobs = ch.get("jobs") or {}
        if isinstance(jobs, dict):
            for t in (jobs.get("primary") or []) + (jobs.get("temporary") or []):
                if not isinstance(t, dict):
                    continue
                th = _clean_handle_tuple(t.get("target_handle"))
                if th:
                    t_name = t.get("task_name") or str(t.get("task_code") or "")
                    entry_str = f"{c_name} ({t_name})" if t_name else str(c_name)
                    if entry_str not in workers_by_handle[th]:
                        workers_by_handle[th].append(entry_str)

    rows: list[dict[str, Any]] = []

    def walk(b: dict[str, Any], parent_settlement: tuple[str | None, str] | None,
             parent_zone: str | None, parent_id: str | None, parent_name: str | None) -> None:
        zone = b.get("zone") or parent_zone
        settlement, how = _settlement(b, zone, places, parent_settlement, zone_places)
        loc_key = settlement or (ZONE_KEY_FORMAT.format(zone=zone) if zone else "неизвестно")
        tpl_sid = b.get("template_sid")
        stats = _stats(index, tpl_sid)
        b_rec = b_records.get(tpl_sid or "")
        label = b.get("name") or b.get("template_name") or b.get("template") or _label(index, tpl_sid) or "Здание"
        category = b.get("category") or stats.get("building category") or ((b_rec or {}).get("string") or {}).get("building category")

        slots = _building_cost_slots(tpl_sid, b_rec, index)
        deposited = b.get("construction_deposited")
        cost_parts: list[str] = []
        if slots:
            for i, (_s_sid, s_name, req) in enumerate(slots):
                if isinstance(deposited, list) and i < len(deposited) and deposited[i] is not None:
                    cost_parts.append(f"{s_name}: {cell(deposited[i])}/{req}")
                else:
                    cost_parts.append(f"{s_name}: {req}")
        elif isinstance(deposited, list) and any(v for v in deposited if v is not None):
            cost_parts = [f"слот {i}: {cell(v)}" for i, v in enumerate(deposited) if v is not None]

        craft_parts: list[str] = []
        for cq in b.get("craft_queue") or []:
            c_name = cq.get("item_name") or _label(index, cq.get("item_sid")) or cq.get("item_sid")
            prog = cq.get("progress")
            pct = f" ({cell(round(prog * 100, 1))}%)" if isinstance(prog, (int, float)) else ""
            craft_parts.append(f"{c_name}{pct}")

        partial_parts: list[str] = []
        for cp in b.get("craft_partial_items") or []:
            p_name = cp.get("item_name") or _label(index, cp.get("item_sid")) or cp.get("item_sid")
            partial_parts.append(f"{p_name}: {cell(cp.get('amount'))}")

        raw_items = b.get("storage_items") or []
        if raw_items:
            stored_count = sum(int(it.get("quantity") or 1) for it in raw_items)
        else:
            stored_count = sum(int(r.get("quantity") or 0) for r in b.get("storage") or [])

        bh = _clean_handle_tuple(b.get("handle"))
        workers = _join(sorted(workers_by_handle.get(bh, []))) if bh else None

        upgrades = stats.get("refs:upgrades to") or {u: v for u, v in ((b_rec or {}).get("refs") or {}).get("upgrades to", [])}
        b_ints = (b_rec or {}).get("int") or {}
        power_out = stats.get("power output") if "power output" in stats else b_ints.get("power output")
        power_cap = stats.get("power capacity") if "power capacity" in stats else b_ints.get("power capacity")

        b_id = str(b.get("id") or "")
        rows.append({
            "settlement": settlement,
            "location_key": loc_key,
            "settlement_status": how,
            "zone": zone,
            "instance_id": b_id,
            "parent_instance_id": parent_id,
            "parent_name": parent_name,
            "name": label,
            "template_sid": tpl_sid,
            "category": category,
            "is_interior": parent_id is not None,
            "floor": b.get("floor"),
            "complete": b.get("complete"),
            "destroyed": b.get("destroyed"),
            "broken": b.get("broken"),
            "construction_progress_raw": b.get("construction_progress_raw"),
            "construction_materials": _join(cost_parts),
            "power_on": b.get("power_on"),
            "gets_battery": b.get("gets_battery"),
            "battery_charge": b.get("battery_charge"),
            "tpl_power_output": power_out,
            "tpl_power_capacity": power_cap,
            "is_paused": b.get("is_paused"),
            "is_repeat": b.get("is_repeat"),
            "production_progress": b.get("production_progress"),
            "resource_mult": b.get("resource_mult"),
            "mining_resource_level": b.get("mining_resource_level"),
            "active_craft": _join(craft_parts),
            "partial_ingredients": _join(partial_parts),
            "stored_items_count": stored_count if stored_count > 0 else None,
            "assigned_workers": workers,
            "upgrades_to": _join(_label(index, u) or u for u in upgrades),
        })
        next_parent = (settlement, how) if settlement else parent_settlement
        for sub in b.get("interior") or []:
            walk(sub, next_parent, zone, b_id, label)

    for b in buildings:
        walk(b, None, None, None, None)

    rows.sort(key=lambda r: (
        str(r["location_key"] or ""), str(r["zone"] or ""),
        str(r["parent_instance_id"] or ""), str(r["instance_id"] or ""),
    ))
    columns = [
        ("settlement", "Поселение; пусто, если не определено"),
        ("location_key", "Ключ места: поселение или «зона N (поселение не определено)»"),
        ("settlement_status", "Способ привязки: in_location, town, near_location, zone_location, zone"),
        ("zone", "Зона карты"),
        ("instance_id", "ID экземпляра постройки или жилы в сохранении"),
        ("parent_instance_id", "ID родительского здания для интерьерного объекта; пусто у верхнего уровня"),
        ("parent_name", "Название родительского здания"),
        ("name", "Название постройки/станка/жилы (ru)"),
        ("template_sid", "sid шаблона BUILDING в данных игры"),
        ("category", "Категория постройки (`building category`: Production, Research, Crafting, Power, Storage, Mining, Training, Turret...)"),
        ("is_interior", "true - объект внутри здания (`interior`); false - верхний уровень"),
        ("floor", "Этаж внутри здания (`floornum`)"),
        ("complete", "true - постройка завершена; false - недострой"),
        ("destroyed", "true - разрушена"),
        ("broken", "true - сломана (`broken`)"),
        ("construction_progress_raw", "Сырой прогресс постройки из сохранения"),
        ("construction_materials", "Смета и фактически вложенные материалы по слотам (`Материал: вложено/требуется`), через `; `"),
        ("power_on", "Питание включено (`power on`)"),
        ("gets_battery", "Подключено к сети/аккумуляторам (`gets battery`)"),
        ("battery_charge", "Текущий заряд аккумулятора в сейве (`batterycharge`)"),
        ("tpl_power_output", "Паспортная выработка (>0) или потребление (<0) энергии из шаблона BUILDING (`power output`)"),
        ("tpl_power_capacity", "Паспортная ёмкость аккумулятора из шаблона BUILDING (`power capacity`)"),
        ("is_paused", "Работа приостановлена (`pause`)"),
        ("is_repeat", "Включён повтор очереди крафта (`repeat`)"),
        ("production_progress", "Сырой прогресс цикла производства (`production amount`)"),
        ("resource_mult", "Качество ресурсной жилы (`resource mult`, 0..1 для MINING)"),
        ("mining_resource_level", "Уровень ресурса разработки (`mining resource level`)"),
        ("active_craft", "Активная очередь крафта станка `предмет (прогресс%)` из `current0..N`, через `; `"),
        ("partial_ingredients", "Вложенные в станок незавершённые ингредиенты `предмет: количество` из `craft partial item`, через `; `"),
        ("stored_items_count", "Суммарное количество предметов во внутреннем контейнере объекта; подробно - storage.csv"),
        ("assigned_workers", "Назначенные на этот объект работники отряда `Имя (ЗАДАЧА)` по совпадению target_handle"),
        ("upgrades_to", "Следующая ступень улучшения постройки из шаблона BUILDING (`upgrades to`)"),
    ]
    return Table(
        "buildings.csv",
        "location_key, zone, parent_instance_id, instance_id",
        "Все постройки, интерьерные станки, турели, генераторы, контейнеры и разрабатываемые рудные жилы игрока",
        columns, rows,
    )


def _research_state_maps(current: dict[str, Any]) -> tuple[dict[str, int], dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str], int]:
    res = current.get("research") or {}
    completed_levels: dict[str, int] = {}
    paid_sids: set[str] = set()
    for e in res.get("completed") or []:
        if e.get("item_blueprint"):
            continue
        base_sid = str(e.get("base_sid") or e.get("sid") or "")
        if not base_sid:
            continue
        lvl = int(e.get("repeat_level") or 1)
        completed_levels[base_sid] = max(completed_levels.get(base_sid, 0), lvl)
        if e.get("paid"):
            paid_sids.add(base_sid)
    in_progress_map: dict[str, dict[str, Any]] = {}
    for e in res.get("in_progress") or []:
        base_sid = str(e.get("base_sid") or e.get("sid") or "")
        if base_sid:
            in_progress_map[base_sid] = e
            if e.get("paid"):
                paid_sids.add(base_sid)
    queue_map: dict[str, dict[str, Any]] = {}
    for e in res.get("queue") or []:
        base_sid = str(e.get("base_sid") or e.get("sid") or "")
        if base_sid:
            queue_map[base_sid] = e
            if e.get("paid"):
                paid_sids.add(base_sid)
    for e in res.get("paid") or []:
        base_sid = str(e.get("base_sid") or e.get("sid") or "")
        if base_sid:
            paid_sids.add(base_sid)
    tech_level = int(res.get("tech_level") or 1)
    return completed_levels, in_progress_map, queue_map, paid_sids, tech_level


def research_table(current: dict[str, Any], records: dict[str, Any] | None,
                   index: dict[str, list[Any]] | None) -> Table:
    completed_levels, in_progress_map, queue_map, paid_sids, tech_level = _research_state_maps(current)
    catalog = _production(records).get("research") or []
    rows: list[dict[str, Any]] = []

    for rec in catalog:
        sid = rec["sid"]
        ints, floats, bools, strings = (rec.get("int") or {}), (rec.get("float") or {}), (rec.get("bool") or {}), (rec.get("string") or {})
        refs = rec.get("refs") or {}
        repeats = int(ints.get("repeats") or 0)
        max_repeats = repeats if repeats > 1 else 1
        done_lvl = completed_levels.get(sid, 0)
        req_pairs = refs.get("requirements") or []
        missing_reqs = [
            _label(index, r_sid) or r_sid
            for r_sid, r_v in req_pairs
            if completed_levels.get(r_sid, 0) < max(1, int(r_v[0]) if r_v else 1)
        ]
        lvl_req = ints.get("level")
        bp_only = bool(bools.get("blueprint only"))

        if sid in in_progress_map:
            status = "in_progress"
            prog = in_progress_map[sid].get("progress_raw")
        elif sid in queue_map:
            status = "queued"
            prog = queue_map[sid].get("progress_raw")
        elif done_lvl >= max_repeats:
            status = "completed"
            prog = None
        elif missing_reqs:
            status = "locked_prereq"
            prog = None
        elif isinstance(lvl_req, int) and lvl_req > tech_level:
            status = "locked_tech_level"
            prog = None
        elif bp_only and sid not in paid_sids and done_lvl == 0:
            status = "locked_blueprint"
            prog = None
        else:
            status = "available"
            prog = None

        cost_parts = [
            f"{_label(index, c_sid) or c_sid}: {c_v[0] if c_v else 1}"
            for c_sid, c_v in (refs.get("cost") or [])
        ]
        req_parts = [
            f"{_label(index, r_sid) or r_sid}" + (f" (ур. {r_v[0]})" if r_v and r_v[0] > 1 else "")
            for r_sid, r_v in req_pairs
        ]
        enable_bld = [_label(index, b_sid) or b_sid for b_sid, _v in (refs.get("enable buildings") or [])]
        improve_bld = [_label(index, b_sid) or b_sid for b_sid, _v in (refs.get("improve buildings") or [])]
        imp_effects: list[str] = []
        if ints.get("power increase"):
            imp_effects.append(f"power+{ints['power increase']}")
        if ints.get("power capacity increase"):
            imp_effects.append(f"capacity+{ints['power capacity increase']}")
        if floats.get("production mult") not in (None, 1.0, 0.0):
            imp_effects.append(f"prod_mult={cell(floats['production mult'])}")

        crafts_cnt = sum(len(refs.get(cat) or []) for cat in RESEARCH_CRAFT_REFS)
        en, ru = _names(index, sid)
        rows.append({
            "sid": sid,
            "name_en": rec.get("name") or en or None,
            "name_ru": ru or rec.get("name") or None,
            "category": strings.get("category") or None,
            "status": status,
            "level": lvl_req,
            "tech_level_upgrade": True if bools.get("is level upgrade") else None,
            "blueprint_only": True if bp_only else None,
            "paid": True if sid in paid_sids else None,
            "progress_raw": prog,
            "completed_level": done_lvl,
            "max_repeats": max_repeats,
            "time_hours": ints.get("time"),
            "money_cost": ints.get("money") if ints.get("money") else None,
            "cost_items": _join(cost_parts),
            "requirements": _join(req_parts),
            "missing_requirements": _join(missing_reqs),
            "enable_buildings": _join(enable_bld),
            "improve_buildings": _join(improve_bld),
            "building_improvements": _join(imp_effects),
            "enable_crafts_count": crafts_cnt if crafts_cnt > 0 else None,
        })

    rows.sort(key=lambda r: (str(r["category"] or "~"), int(r["level"] or 0), str(r["sid"])))
    columns = [
        ("sid", "sid записи RESEARCH (тип 21)"),
        ("name_en", "Название технологии (en)"),
        ("name_ru", "Название технологии (ru)"),
        ("category", "Технологическая ветка (`category`: Core, Farming, Mining, Power, Defence, Smithing, Robotics...)"),
        ("status", "Статус у игрока: completed (изучена), in_progress (изучается сейчас), queued (в очереди стенда), "
                   "available (доступна к изучению), locked_prereq (не выполнены requirements), locked_tech_level "
                   "(нужен стенд выше текущего tech_level), locked_blueprint (нужен чертёж)"),
        ("level", "Требуемый уровень научного стенда (`level`)"),
        ("tech_level_upgrade", "true - повышает технологический уровень (`is level upgrade`)"),
        ("blueprint_only", "true - открывается только применением чертежа (`blueprint only`)"),
        ("paid", "true - ресурсы за исследование уже вложены в стенд (`paid#`)"),
        ("progress_raw", "Сырой прогресс изучения из сохранения (`current_progress`)"),
        ("completed_level", "Изученный уровень (0 - не изучена; 1..max_repeats для многократных исследований)"),
        ("max_repeats", "Максимальное число уровней изучения (`repeats`; 1 для однократных)"),
        ("time_hours", "Базовое время изучения, ч (`time`)"),
        ("money_cost", "Денежная стоимость, кат. (`money`)"),
        ("cost_items", "Предметы, расходуемые на изучение (`refs:cost`: `предмет: количество`), через `; `"),
        ("requirements", "Предшествующие технологии (`refs:requirements`), через `; `"),
        ("missing_requirements", "Неизученные предшествующие технологии из `requirements`, блокирующие запуск"),
        ("enable_buildings", "Открываемые постройки и станки (`refs:enable buildings`), через `; `"),
        ("improve_buildings", "Улучшаемые постройки (`refs:improve buildings`), через `; `"),
        ("building_improvements", "Бонусы улучшения построек (`power increase`, `power capacity increase`, `production mult`)"),
        ("enable_crafts_count", "Число открываемых рецептов крафта предметов, оружия, брони, арбалетов, рюкзаков и протезов"),
    ]
    return Table(
        "research.csv",
        "category, level, sid",
        "Полное дерево технологий игры (RESEARCH) со статусом изучения в кампании, стоимостью, предпосылками и открываемыми объектами",
        columns, rows,
    )


def build_catalog_table(current: dict[str, Any], records: dict[str, Any] | None,
                        index: dict[str, list[Any]] | None) -> Table:
    completed_levels, in_progress_map, queue_map, _paid_sids, _tech_level = _research_state_maps(current)
    prod = _production(records)
    res_catalog = prod.get("research") or []
    bld_catalog = {r["sid"]: r for r in prod.get("buildings") or []}
    func_map = {r["sid"]: r for r in prod.get("functionalities") or []}
    farm_map = {r["sid"]: r for r in prod.get("farms") or []}

    # Какие технологии открывают каждую постройку
    unlockers: dict[str, list[str]] = defaultdict(list)
    for r in res_catalog:
        r_sid = r["sid"]
        for b_sid, _v in ((r.get("refs") or {}).get("enable buildings") or []):
            if r_sid not in unlockers[b_sid]:
                unlockers[b_sid].append(r_sid)

    # Подсчёт уже построенных игроком экземпляров по template_sid
    built_complete: dict[str, int] = defaultdict(int)
    built_incomplete: dict[str, int] = defaultdict(int)

    def count_player_buildings(items: list[dict[str, Any]]) -> None:
        for b in items:
            tsid = b.get("template_sid")
            if tsid:
                if b.get("complete") is False:
                    built_incomplete[tsid] += 1
                else:
                    built_complete[tsid] += 1
            count_player_buildings(b.get("interior") or [])

    count_player_buildings((current.get("property") or {}).get("buildings") or [])

    target_sids = sorted(set(unlockers.keys()) | (set(built_complete.keys()) | set(built_incomplete.keys())) & set(bld_catalog.keys()))
    rows: list[dict[str, Any]] = []

    for sid in target_sids:
        rec = bld_catalog.get(sid)
        if not rec:
            continue
        ints, floats, bools, strings = (rec.get("int") or {}), (rec.get("float") or {}), (rec.get("bool") or {}), (rec.get("string") or {})
        refs = rec.get("refs") or {}
        req_res = unlockers.get(sid, [])
        if req_res:
            if any(completed_levels.get(r_sid, 0) >= 1 or r_sid == "5359-gamedata.base" for r_sid in req_res):
                unlock_status = "unlocked"
            elif any(r_sid in in_progress_map or r_sid in queue_map for r_sid in req_res):
                unlock_status = "in_queue"
            else:
                unlock_status = "locked"
        else:
            unlock_status = "world_or_town"

        unlocked_by_labels = [
            "_Default Start (базовая)" if r_sid == "5359-gamedata.base" else f"{_label(index, r_sid) or r_sid} ({r_sid})"
            for r_sid in req_res
        ]

        slots = _building_cost_slots(sid, rec, index)
        cost_str = _join(f"{s_name}: {req}" for _s_sid, s_name, req in slots)
        bm_total = ints.get("build materials")
        bs_mult = floats.get("build speed mult")
        build_hours = round(bm_total * bs_mult, 2) if isinstance(bm_total, int) and bm_total > 0 and isinstance(bs_mult, (int, float)) else None

        # Функциональность (тип 62) и данные фермы (тип 97)
        func_sids = [f_sid for f_sid, _v in (refs.get("functionality") or []) if f_sid in func_map]
        func_rec = func_map[func_sids[0]] if func_sids else {}
        f_ints, f_floats, f_refs = (func_rec.get("int") or {}), (func_rec.get("float") or {}), (func_rec.get("refs") or {})

        farm_sids = [fm_sid for fm_sid, _v in (refs.get("farm data") or []) if fm_sid in farm_map]
        farm_rec = farm_map[farm_sids[0]] if farm_sids else {}
        fm_ints, fm_floats = (farm_rec.get("int") or {}), (farm_rec.get("float") or {})

        max_ops = ints.get("max operators")
        if max_ops is None or max_ops < 0:
            max_ops = f_ints.get("max operators")

        consumes_parts = [
            f"{_label(index, c_sid) or c_sid} (rate={c_v[1] if len(c_v) > 1 else 100}%)"
            for c_sid, c_v in (f_refs.get("consumes") or [])
        ]
        if fm_floats.get("consumption rate"):
            consumes_parts.append(f"Вода (consumption rate={cell(fm_floats['consumption rate'])}/сут)")

        produces_parts = [_label(index, p_sid) or p_sid for p_sid, _v in (f_refs.get("produces") or [])]
        crafts_cnt = sum(len(f_refs.get(cat) or []) for cat in BUILDING_CRAFT_REFS)

        farm_info: list[str] = []
        if farm_rec:
            climates = [f"{k}={cell(fm_floats[k])}" for k in ("arid", "green", "swamp") if fm_floats.get(k)]
            if climates:
                farm_info.append("/".join(climates))
            if fm_floats.get("minimum fertility") is not None:
                farm_info.append(f"min_fert={cell(fm_floats['minimum fertility'])}")
            if fm_floats.get("growth time") is not None:
                farm_info.append(f"growth={cell(fm_floats['growth time'])}h")
            if fm_ints.get("amount") is not None:
                farm_info.append(f"plants={fm_ints['amount']}")

        en, ru = _names(index, sid)
        rows.append({
            "sid": sid,
            "name_en": rec.get("name") or en or None,
            "name_ru": ru or rec.get("name") or None,
            "building_category": strings.get("building category") or None,
            "is_interior": bools.get("is interior furniture"),
            "is_node": True if bools.get("is node") else None,
            "unlock_status": unlock_status,
            "unlocked_by": _join(unlocked_by_labels),
            "built_complete_count": built_complete.get(sid, 0),
            "built_incomplete_count": built_incomplete.get(sid, 0),
            "construction_materials": cost_str,
            "build_materials_total": bm_total,
            "build_speed_mult": bs_mult,
            "build_hours": build_hours,
            "power_output": ints.get("power output"),
            "power_capacity": ints.get("power capacity"),
            "max_operators": max_ops,
            "output_rate": floats.get("output rate"),
            "production_mult": f_floats.get("production mult"),
            "function_code": f_ints.get("function"),
            "stat_used_code": f_ints.get("stat used"),
            "world_resource_mining": f_ints.get("world resource mining"),
            "consumes": _join(consumes_parts),
            "produces": _join(produces_parts),
            "crafts_count": crafts_cnt if crafts_cnt > 0 else None,
            "farm_profile": _join(farm_info),
            "upgrades_to": _join(_label(index, u_sid) or u_sid for u_sid, _v in (refs.get("upgrades to") or [])),
        })

    rows.sort(key=lambda r: (str(r["building_category"] or "~"), str(r["sid"])))
    columns = [
        ("sid", "sid записи BUILDING (тип 0)"),
        ("name_en", "Название постройки (en)"),
        ("name_ru", "Название постройки (ru)"),
        ("building_category", "Категория в меню строительства (`building category`)"),
        ("is_interior", "true - устанавливается только внутри зданий (`is interior furniture`); false - наружная постройка"),
        ("is_node", "true - природный ресурсный узел (`is node`)"),
        ("unlock_status", "Статус доступности игроку: unlocked (открыта и доступна к постройке), in_queue (технология в очереди), "
                          "locked (технология не изучена), world_or_town (городское здание или природная жила)"),
        ("unlocked_by", "Открывающие технологии из RESEARCH (`enable buildings`)"),
        ("built_complete_count", "Количество уже достроенных экземпляров у игрока в текущем сохранении"),
        ("built_incomplete_count", "Количество заложенных (недостроенных) экземпляров у игрока"),
        ("construction_materials", "Точная смета постройки 1 единицы (`refs:construction` либо стандартные Стройматериалы по `build materials`), через `; `"),
        ("build_materials_total", "Поле `build materials` шаблона"),
        ("build_speed_mult", "Множитель времени строительства (`build speed mult`)"),
        ("build_hours", "Базовое время строительства, ч (`build materials * build speed mult`)"),
        ("power_output", "Выработка (>0) или потребление (<0) энергии (`power output`)"),
        ("power_capacity", "Ёмкость хранения энергии в секундах (`power capacity`)"),
        ("max_operators", "Максимальное число операторов (`max operators`)"),
        ("output_rate", "Базовая скорость/выход (`output rate`)"),
        ("production_mult", "Множитель скорости производства или расхода топлива (`production mult` из BUILDING_FUNCTIONALITY)"),
        ("function_code", "Код функции `function` из BUILDING_FUNCTIONALITY (BuildingFunction)"),
        ("stat_used_code", "Код используемого навыка `stat used` из BUILDING_FUNCTIONALITY (StatsEnumerated)"),
        ("world_resource_mining", "Код подземного ресурса `world resource mining` (0 - нет, 1+ - вода/плодородие/руда/камень/топливо)"),
        ("consumes", "Потребляемое сырьё (`refs:consumes` или расход воды фермы)"),
        ("produces", "Производимый ресурс для автоматических/добывающих построек (`refs:produces`)"),
        ("crafts_count", "Число доступных рецептов крафта на станке (`* crafts`)"),
        ("farm_profile", "Параметры ферм из FARM_DATA: климат (`arid/green/swamp`), `min_fert`, `growth`, `plants`"),
        ("upgrades_to", "Следующая ступень улучшения (`refs:upgrades to`)"),
    ]
    return Table(
        "build_catalog.csv",
        "building_category, sid",
        "Каталог и смета всех строящихся объектов игры (BUILDING): статус открытия, стройматериалы, энергобаланс, входы/выходы и апгрейды",
        columns, rows,
    )


def biome_resources_table(current: dict[str, Any], locations: dict[str, Any] | None,
                          world_ref: dict[str, Any] | None, records: dict[str, Any] | None,
                          index: dict[str, list[Any]] | None) -> Table:
    biomes = _production(records).get("biome_resources") or []
    discovered_ids = {
        str(d.get("instance_id"))
        for d in ((current.get("world_state") or {}).get("town_discovery") or [])
        if d.get("discovered") and d.get("instance_id")
    }
    locs_by_region_en: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for loc in (world_ref or {}).get("locations") or []:
        reg_en = loc.get("region")
        if reg_en:
            locs_by_region_en[str(reg_en)].append(loc)

    # Постройки игрока по регионам (через locations.buildings) и разрабатываемые рудные жилы (MINING)
    bldg_region_en: dict[str, str] = {}
    for lb in (locations or {}).get("buildings") or []:
        bkey = lb.get("building_key")
        reg_en = (lb.get("region") or {}).get("name_en")
        if bkey and reg_en:
            bldg_region_en[str(bkey)] = str(reg_en)

    player_bld_counts: dict[str, int] = defaultdict(int)
    player_mining_by_reg: dict[str, list[str]] = defaultdict(list)
    for b in (current.get("property") or {}).get("buildings") or []:
        bkey = f"{b.get('zone')}:{b.get('id')}"
        reg_en = bldg_region_en.get(bkey)
        if not reg_en:
            continue
        player_bld_counts[reg_en] += 1
        cat = b.get("category") or _stats(index, b.get("template_sid")).get("building category")
        if cat == "MINING" or (b.get("resource_mult") is not None and not b.get("interior")):
            b_name = b.get("name") or b.get("template_name") or b.get("template") or "Жила"
            mult = cell(b.get("resource_mult"))
            player_mining_by_reg[reg_en].append(f"{b_name} [{b.get('zone')}, mult={mult}]")

    rows: list[dict[str, Any]] = []
    for rec in biomes:
        sid = rec["sid"]
        name_en = rec.get("name") or _names(index, sid)[0] or ""
        name_ru = _names(index, sid)[1] or name_en or None
        b_ints, b_floats = (rec.get("int") or {}), (rec.get("float") or {})
        env = rec.get("env_resources") or {}
        e_ints, e_floats = (env.get("int") or {}), (env.get("float") or {})

        # Ветровой режим и погодные угрозы по сезонам (SEASON -> WEATHER -> WEATHER_EFFECT)
        wind_mins: list[float] = []
        wind_maxs: list[float] = []
        weighted_wind_sum = 0.0
        weighted_wind_total = 0.0
        affect_codes: set[int] = set()

        for s in rec.get("seasons_detail") or []:
            s_weight = float(s.get("duration_weight") or 1.0)
            if s_weight <= 0:
                s_weight = 1.0
            weathers = s.get("weathers") or []
            prob_total = sum(max(float(w.get("prob") or 1.0), 0.0) for w in weathers) or 1.0
            for w in weathers:
                w_prob = max(float(w.get("prob") or 1.0), 0.0) / prob_total
                w_min = w.get("wind_speed_min")
                w_max = w.get("wind_speed_max")
                if isinstance(w_min, (int, float)):
                    wind_mins.append(float(w_min))
                if isinstance(w_max, (int, float)):
                    wind_maxs.append(float(w_max))
                if isinstance(w_min, (int, float)) and isinstance(w_max, (int, float)):
                    weighted_wind_sum += s_weight * w_prob * ((float(w_min) + float(w_max)) / 2.0)
                    weighted_wind_total += s_weight * w_prob
                aff = w.get("affect_type")
                if isinstance(aff, int) and aff > 0:
                    affect_codes.add(aff)
                for ef_aff in w.get("effect_affect_types") or []:
                    if isinstance(ef_aff, int) and ef_aff > 0:
                        affect_codes.add(ef_aff)

        sorted_codes = sorted(affect_codes)
        decoded_hazards = [WEATHER_RU.get(c, f"код {c} (не установлен)") for c in sorted_codes]
        reg_locs = locs_by_region_en.get(name_en, [])
        disc_count = sum(1 for loc in reg_locs if str(loc.get("instance_id")) in discovered_ids)

        rows.append({
            "biome_sid": sid,
            "name_en": name_en or None,
            "name_ru": name_ru,
            "color_index": b_ints.get("index"),
            "locations_total": len(reg_locs),
            "locations_discovered": disc_count,
            "player_buildings_count": player_bld_counts.get(name_en, 0),
            "player_mining_nodes": _join(sorted(player_mining_by_reg.get(name_en, []))),
            "climate_arid": e_floats.get("arid"),
            "climate_green": e_floats.get("green"),
            "climate_swamp": e_floats.get("swamp"),
            "farming_min": e_floats.get("farming min"),
            "farming_mult": e_floats.get("farming mult"),
            "farming_altitude_min": e_ints.get("farming altitude min"),
            "farming_altitude_max": e_ints.get("farming altitude max"),
            "water_min": e_floats.get("water min"),
            "water_mult": e_floats.get("water mult"),
            "water_altitude_min": e_ints.get("water altitude min"),
            "water_altitude_max": e_ints.get("water altitude max"),
            "farm_water_usage": b_floats.get("farm water usage"),
            "iron_mult": e_floats.get("iron mult"),
            "copper_mult": e_floats.get("copper mult"),
            "stone_mult": e_floats.get("stone mult"),
            "stone_noise_cutoff": e_floats.get("stone noise cutoff"),
            "carbon_mult": e_floats.get("carbon mult"),
            "wind_speed_min": min(wind_mins) if wind_mins else None,
            "wind_speed_max": max(wind_maxs) if wind_maxs else None,
            "wind_speed_weighted_avg": round(weighted_wind_sum / weighted_wind_total, 2) if weighted_wind_total > 0 else None,
            "acidic_ground": b_floats.get("acidic ground"),
            "acidic_water": b_floats.get("acidic water"),
            "weather_affect_codes": _join(sorted_codes),
            "weather_hazards": _join(decoded_hazards),
        })

    rows.sort(key=lambda r: str(r["biome_sid"]))
    columns = [
        ("biome_sid", "sid записи BIOME_GROUP (тип 95)"),
        ("name_en", "Название региона (en)"),
        ("name_ru", "Название региона (ru)"),
        ("color_index", "Цветовой код региона на карте (`index`)"),
        ("locations_total", "Всего стационарных локаций в регионе по справочнику мира (`world_reference.json`)"),
        ("locations_discovered", "Открыто локаций игроком (`discovered = true` в `quick.save`)"),
        ("player_buildings_count", "Число верхнеуровневых построек и рудных точек игрока в регионе"),
        ("player_mining_nodes", "Уже разрабатываемые игроком рудные точки (`MINING`) в регионе с их `resource_mult`"),
        ("climate_arid", "Доля засушливого климата (`arid` из ENVIRONMENT_RESOURCES)"),
        ("climate_green", "Доля зелёного климата (`green` из ENVIRONMENT_RESOURCES)"),
        ("climate_swamp", "Доля болотного климата (`swamp` из ENVIRONMENT_RESOURCES)"),
        ("farming_min", "Минимальное плодородие почвы в регионе (`farming min`)"),
        ("farming_mult", "Максимальный множитель плодородия почвы (`farming mult`)"),
        ("farming_altitude_min", "Нижняя граница высоты плодородия (`farming altitude min`)"),
        ("farming_altitude_max", "Верхняя граница высоты плодородия (`farming altitude max`)"),
        ("water_min", "Минимальный уровень грунтовых вод (`water min`)"),
        ("water_mult", "Максимальный множитель грунтовых вод (`water mult`)"),
        ("water_altitude_min", "Нижняя граница высоты грунтовых вод (`water altitude min`)"),
        ("water_altitude_max", "Верхняя граница высоты грунтовых вод (`water altitude max`)"),
        ("farm_water_usage", "Множитель расхода воды фермами в регионе (`farm water usage`)"),
        ("iron_mult", "Множитель железной руды в регионе (`iron mult`)"),
        ("copper_mult", "Множитель медной руды в регионе (`copper mult`)"),
        ("stone_mult", "Множитель камня в регионе (`stone mult`)"),
        ("stone_noise_cutoff", "Порог Perlin-шума пятен камня (`stone noise cutoff`; меньше — крупнее пятна)"),
        ("carbon_mult", "Множитель углерода/топлива (`carbon mult`)"),
        ("wind_speed_min", "Минимальная скорость ветра по сезонам региона (`wind speed min` из WEATHER)"),
        ("wind_speed_max", "Максимальная скорость ветра по сезонам региона (`wind speed max` из WEATHER)"),
        ("wind_speed_weighted_avg", "Средневзвешенная скорость ветра по длительности сезонов и вероятностям погоды"),
        ("acidic_ground", "Кислотность почвы (`acidic ground`, 0..1: обжигает ноги без обуви)"),
        ("acidic_water", "Кислотность воды (`acidic water`, 0..1)"),
        ("weather_affect_codes", "Коды погодных угроз в сезонах региона (`affect type` из WEATHER и WEATHER_EFFECT)"),
        ("weather_hazards", "Расшифровка погодных угроз (1 песчаная буря, 2 кислотный дождь, 3 жара/лучи, 4 газ; сопоставимо с `races.csv`)"),
    ]
    return Table(
        "biome_resources.csv",
        "biome_sid",
        "Ресурсный, климатический и ветровой профиль регионов (BIOME_GROUP + ENVIRONMENT_RESOURCES + SEASON/WEATHER)",
        columns, rows,
    )


# --------------------------------------------------------------------------- сборка и index.md

def render_index(current: dict[str, Any], snapshot_id: str, tables: list[Table], game_mods: list[str] | None,
                 game_notes: Sequence[str]) -> str:
    world = current.get("world") or {}
    game_time = (f"День {world['day']}, {world.get('time')}" if world.get("day") is not None else "не получено")
    lines = [
        "# Табличные выгрузки", "",
        f"- snapshot_id: `{snapshot_id}`",
        f"- Игровое время: {game_time}",
        "- Формат: CSV, UTF-8 без BOM, LF, RFC 4180, разделитель - запятая; первая строка - заголовок.",
        "- Пустая ячейка - значение отсутствует или не применимо; числа с точкой без разделителей разрядов, "
        "булевы `true`/`false`; списки в ячейке разделены `; `.",
        "- Строки отсортированы по ключу; меток времени в таблицах нет; связи между таблицами - по идентификаторам (sid, id), не по именам.",
        f"- Справочник игры (шаблоны, цены, торговля, реакции): "
        + (", ".join(game_mods) if game_mods else "недоступен, таблицы на его данных пусты или без колонок шаблона"),
        "", "## Таблицы", "",
        "| Файл | Строк | Ключ сортировки | Содержание |", "|---|---|---|---|",
    ]
    for t in tables:
        lines.append(f"| `{t.name}` | {len(t.rows)} | {t.sort_key} | {t.purpose} |")
    lines += ["", "## Недоступные и неопубликованные поля", ""]
    lines += [f"- {n}" for n in game_notes]
    lines += [
        CALC_NOTE + " Значения шаблона (`tpl_*`), множители производителя и поправки грейда даны отдельными колонками.",
        "- Предметы персонажей поэкземплярно - `inventory.csv` (контейнеры body, belt, backpack, inventory; ключ сравнения снимков "
        "(character_id, base_sid, level, slot, dup_no), `item_sid` действует внутри снимка). Оружие, броня и протезы "
        "независимо от места - `equipment.csv` (`location_kind`). Вес экземпляров и параметры рюкзаков, здоровье по частям "
        "тела в таблицы не входят. Построенные здания, интерьерные станки, очереди крафта и разрабатываемые жилы - `buildings.csv`; "
        "полное дерево технологий со статусом изучения - `research.csv`; смета стройматериалов и ТТХ всех построек меню "
        "строительства - `build_catalog.csv`; ресурсно-климатический профиль регионов (плодородие, вода, руды, ветер, кислотность) - "
        "`biome_resources.csv` (локальные пятна кнопки «Разведка» в рантайме вычисляются процедурным шумом и в сохранение не записываются).",
        "- `races.csv`: поля записей RACE после слияния файлов в порядке загрузки; пустая ячейка - поле не хранится "
        "(действует значение по умолчанию fcs.def). Расшифровка кодов погоды 1-4 сверена с карточками брони, прочие не расшифрованы.",
        "- Смысл `v[1]`, `v[2]` у ссылок `world state`/`NPC is` в документах игры не описан; значения публикуются без трактовки.",
        "- Справочник юнитов (`unit_templates.csv`, `unit_template_gear.csv`, `squad_templates.csv`, `squad_members.csv`, "
        "`squad_spawns.csv`, `faction_relations.csv`, `faction_hostility.csv`): значения шаблонов как хранятся в данных набора "
        "модов снимка, без вычисленных оценок; итоговые навыки персонажа игра получает с разбросом `stats_randomise`. "
        "Не публикуется: враждебность по расе, полу и протезам (в данных такого поля нет), привязка групп биомов к зонам карты "
        "(в записях её нет), имена кодов `NPC class`, `armour grade`, `fundamental type`, `slave` (перечисления не заданы в данных).",
        "- Итоговые цены продажи и покупки в городах не вычисляются: связь город - культура задана записями, "
        "но остальные множители (отряд-торговец, константы, разброс) не сверены с игрой.",
        "- Для зданий без поселения в `storage.csv` колонка `settlement` пуста, а `location_key` содержит «зона N (поселение не определено)».",
        "- `unique_entities.csv`: сводный реестр уникальных сущностей (именованные NPC, боссы, оружие Мейто) со статусом обнаружения, привязкой к локациям и состоянием в отряде.",
        "", "## Коды state_code уникальных NPC", "",
        "Факт игры (fcs.def, WORLD_EVENT_STATE, поле `NPC is`): 0 - мёртв, 1 - жив и свободен, 2 - в плену.", "",
        "## Словарь колонок",
    ]
    for t in tables:
        lines += ["", f"### {t.name}", "", "| Колонка | Значение |", "|---|---|"]
        lines += [f"| `{name}` | {desc.replace('|', '/')} |" for name, desc in t.columns]
    lines.append("")
    return "\n".join(lines)


def build_tables(
    current: dict[str, Any],
    locations: dict[str, Any] | None,
    snapshot_id: str,
    game_index: dict[str, list[Any]] | None = None,
    game_records: dict[str, Any] | None = None,
    game_notes: Sequence[str] = (),
    world_ref: dict[str, Any] | None = None,
    unique_entities_registry: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """Файлы tables/: имя -> содержимое. Без справочника игры колонки шаблонов и таблицы реакций пусты; это названо в index.md."""
    if unique_entities_registry is None and current:
        from tools.unique_entities_registry import build_unique_entities_registry
        safe_cur = dict(current)
        safe_cur.setdefault("snapshot_id", snapshot_id)
        safe_wref = dict(world_ref or {})
        safe_wref.setdefault("world_ref_id", "unresolved")
        safe_wref.setdefault("created_at", "1970-01-01T00:00:00Z")
        safe_wref.setdefault("locations", [])
        try:
            unique_entities_registry = build_unique_entities_registry(
                cur=safe_cur,
                world_ref=safe_wref,
                game_records=game_records or {},
                game_index=game_index or {},
            )
        except Exception:
            unique_entities_registry = None

    zone_places = zone_settlements(locations, world_ref)
    tables = [
        squad_table(current, locations),
        equipment_table(current, game_index, locations, zone_places),
        inventory_table(current, game_index),
        storage_table(current, locations, game_index, zone_places),
        buildings_table(current, locations, game_index, game_records, zone_places),
        research_table(current, game_records, game_index),
        build_catalog_table(current, game_records, game_index),
        biome_resources_table(current, locations, world_ref, game_records, game_index),
        item_prices_table(game_index),
        trade_modifiers_table(game_records, game_index),
        unique_npcs_table(current),
        unique_entities_table(unique_entities_registry, current),
        town_overrides_table(current, game_index),
        diplomacy_table(current),
        world_reactions_table(game_records, game_index),
        world_reaction_links_table(current, game_records, game_index),
        unit_templates_table(game_records, game_index),
        unit_template_gear_table(game_records, game_index),
        squad_templates_table(game_records, game_index),
        squad_members_table(game_records, game_index),
        squad_spawns_table(game_records, game_index),
        faction_relations_table(game_records, game_index),
        faction_hostility_table(game_records, game_index),
        races_table(game_records, game_index),
    ]
    mods = ((current.get("meta") or {}).get("game_data") or {}).get("mods") if game_index else None
    notes = list(game_notes)
    if game_index is None:
        notes.append("Справочник игры недоступен: колонки `tpl_*`, множители, цены (`base_price`, `item_prices.csv`) "
                     "и названия по sid не заполнены.")
    if game_records is None:
        notes.append("Записи реакций, торговли и производства недоступны: `trade_modifiers.csv`, `world_reactions.csv`, "
                     "`world_reaction_links.csv`, `races.csv`, `research.csv`, `build_catalog.csv`, `biome_resources.csv`, "
                     "`unit_*.csv`, `squad_templates.csv`, `squad_members.csv`, `squad_spawns.csv`, "
                     "`faction_*.csv` пусты.")
    files = {t.name: csv_bytes(t) for t in tables}
    files[INDEX_NAME] = render_index(current, snapshot_id, tables, mods, notes).encode("utf-8")
    return files


def load_game_data(
    current: dict[str, Any], cache_dir: Path,
) -> tuple[dict[str, list[Any]] | None, dict[str, Any] | None, list[str]]:
    """Индекс и записи игры для набора модов снимка; (None, None, [причина]) при недоступности.

    Без списка модов в снимке не подставляется текущий mods.cfg: набор должен совпадать с сохранением.
    """
    from tools import gamedb

    mods = ((current.get("meta") or {}).get("game_data") or {}).get("mods")
    if not isinstance(mods, list) or not mods:
        return None, None, ["В снимке нет списка модов; справочник игры не загружался."]
    try:
        active = gamedb.active_files(mods)
        if active["missing"] or not active["files"]:
            return None, None, [f"Файлы модов не найдены на диске: {', '.join(active['missing']) or 'все'}."]
        index, _ = gamedb.load_index(str(cache_dir), active["files"])
        records, _ = gamedb.load_records(str(cache_dir), active["files"])
    except (OSError, ValueError, KeyError) as error:
        return None, None, [f"Справочник игры не прочитан: {error}"]
    return index, records, []
