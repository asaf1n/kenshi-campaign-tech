"""Табличные выгрузки published/tables/: CSV и index.md из снимка, locations.json и справочников игры.

Чистые функции без обращения к диску (кроме load_game_data): одинаковые входы дают одинаковые байты.
Колонки calc_* в equipment.csv - значения по формулам itemstats.py/prosthetics.py; при calc_status = verified они
совпадают с карточкой игры (броня, оружие ближнего боя, арбалет, протез; границы - CALC_NOTE). Остальные колонки - исходные значения
сохранения и шаблонов игры. Для величин без сверки (итоговая цена оружия и брони) calc_* колонок нет.
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
from tools.world_context import ZONE_KEY_FORMAT, storage_rows, zone_settlements

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
    ("calc_card_value", "Арбалет: цена карточки, без округления (`value` + (`value 1` - `value`) x (level/100)^2; карточка усекает до целого); у остальных предметов пусто"),
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
    "Колонки `calc_*` в `equipment.csv` - значения по формулам конвертера (itemstats.py, prosthetics.py): значения при "
    "`calc_status` = verified совпадают с карточкой игры; при остальных статусах колонки `calc_*` пусты, а `calc_status` "
    "объясняет причину. Сверка выполнена для: броня (классы лёгкий, средний, тяжёлый; кожа, кольчуга, латы; шлемы, обувь), оружие ближнего "
    "боя, арбалеты (5 партий карточек), протез (нога разведчика). Значения публикуются без округления; совпадение с карточкой игры - равенство после округления до точности интерфейса "
    "(усечение целой части для сопротивлений, веса, нижней границы урона арбалета и цены арбалета; округление для верхней "
    "границы урона и скорости выстрела); в CSV числа записываются с точностью до 6 знаков без хвостовых нулей (это не округление интерфейса); шаблоны берутся после применения модов снимка. calc_* заполняются "
    "только для сверенных сочетаний (`calc_status` = verified): броня - лёгкий и средний класс x кожа, кольчуга; средний x "
    "латы; тяжёлый x латы; оружие ближнего боя - катаны, сабли, древковое, тяжёлое, рубачи с производителями, "
    "встречавшимися на карточках; арбалеты; протезы ног. Сверены: урон, пробитие, сопротивления, "
    "эффективность, покрытие, множители навыков и боевых искусств, разброс, перезарядка, дальность, скорость выстрела и "
    "цена арбалета, ОЗ протеза; UI усекает проценты (-39.99 показано как -39). Не подтверждено: вес Падающего солнца "
    "(карточка 23, расчёт 24.0) и Боевого тесака Древних (карточка 5, расчёт 6), по одному образцу; порог скрытия "
    "атлетики на карточке; граница 59.x у брони Высокого уровня (blunt карточки на 1 ниже); грейды брони 5 «Прото» и 20 "
    "«Низкий». Итоговая цена оружия, брони и сандалий не вычисляется (закон не установлен; базовая цена - в "
    "`item_prices.csv`); множители протезов публикуются только для атлетики, плавания и скрытности."
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
            "calc_shot_speed_ms": stats.get("shot_speed_ms"), "calc_card_value": stats.get("card_value")}


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


def equipment_table(current: dict[str, Any], index: dict[str, list[Any]] | None) -> Table:
    rows: list[dict[str, Any]] = []
    const = _constants(index)
    for c in current.get("characters") or []:
        # Количественные поля экземпляра (quality, sid грейда и производителя) берутся из items[] по слоту и sid.
        instances: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for it in c.get("items") or []:
            slot = WEAPON_SECTIONS.get(it.get("section")) or ARMOUR_SECTIONS.get(it.get("section"))
            if slot and it.get("container") == "inventory":
                instances[(slot, it.get("base_data_sid"))].append(it)

        def take(slot: str, sid: str | None) -> dict[str, Any]:
            queue = instances.get((slot, sid or ""))
            return queue.pop(0) if queue else {}

        def head(kind: str, slot: str, e: dict[str, Any], inst: dict[str, Any]) -> dict[str, Any]:
            return {
                "character_id": c.get("id"), "character_name": c.get("name"), "kind": kind, "slot": slot,
                "item_name": e.get("name"), "item_name_en": e.get("name_en"), "sid": e.get("sid"),
                "item_type": e.get("type"), "level": e.get("level"), "quality": inst.get("quality"),
                "grade": e.get("grade"), "stolen": e.get("stolen"),
            }

        for w in c.get("weapons") or []:
            inst = take(w.get("slot"), w.get("sid"))
            row = head("weapon", w.get("slot"), w, inst)
            stats = _stats(index, w.get("sid"))
            _tpl(row, stats, CROSSBOW_TPL if w.get("type") == "crossbow" else WEAPON_TPL)
            if w.get("type") != "crossbow":
                row["grade_sid"], row["manufacturer"] = inst.get("material_sid") or None, w.get("manufacturer")
                row["manufacturer_sid"] = inst.get("company_sid") or None
                for field, col in GRADE_FIELDS:
                    row[col] = _stats(index, row["grade_sid"]).get(field)
                for field, col in MANUFACTURER_FIELDS:
                    row[col] = _stats(index, row["manufacturer_sid"]).get(field)
            manuf = _stats(index, row.get("manufacturer_sid")) if w.get("type") != "crossbow" else {}
            row |= calc_weapon(w, stats, manuf, const, _names(index, row.get("manufacturer_sid"))[0])
            rows.append(row)
        for slot, a in (c.get("armour") or {}).items():
            if not isinstance(a, dict):
                continue
            inst = take(a.get("slot") or slot, a.get("sid"))
            row = head("armour", a.get("slot") or slot, a, inst)
            row["item_type"] = None
            row["armour_class"], row["armour_material"] = a.get("class"), a.get("material")
            cov = a.get("coverage") or {}
            row["coverage"] = _join(f"{k}={cell(v)}" for k, v in sorted(cov.items()))
            unshown = a.get("template_unshown") or {}
            row["template_unshown"] = _join(f"{k}={cell(v)}" for k, v in sorted(unshown.items()))
            _tpl(row, _stats(index, a.get("sid")), ARMOUR_TPL)
            row |= calc_armour(a, _stats(index, a.get("sid")), index)
            rows.append(row)
        for p in c.get("prosthetics") or []:
            row = head("prosthetic", p.get("limb"), p, {})
            _tpl(row, _stats(index, p.get("sid")), LIMB_TPL)
            row |= calc_prosthetic(p, _stats(index, p.get("sid")))
            rows.append(row)
    rows.sort(key=lambda r: (str(r["character_id"]), r["kind"], str(r["slot"]), str(r["sid"])))

    tpl_fields = list(dict.fromkeys([*WEAPON_TPL, *CROSSBOW_TPL, *ARMOUR_TPL, *LIMB_TPL]))
    columns = [
        ("character_id", "ID персонажа"), ("character_name", "Имя персонажа"),
        ("kind", "weapon, armour или prosthetic"),
        ("slot", "weapon_back/weapon_hip, head/shirt/body/legs/boots или конечность протеза (left_leg...)"),
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
    return Table("equipment.csv", "character_id, kind, slot, sid",
                 "Оружие, броня и протезы персонажей: значения шаблона, множители и итоговые `calc_*`",
                 columns, rows)


# --------------------------------------------------------------------------- склады и цены

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
        })
    rows.sort(key=lambda r: tuple(str(r[k] or "") for k in
                                  ("location_key", "zone", "building_id", "container", "item", "item_sid")))
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
    ]
    return Table("storage.csv", "location_key, zone, building_id, container, item, item_sid",
                 "Предметы в хранилищах зданий игрока", columns, rows)


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
        "- Содержимое инвентаря и рюкзаков персонажей, сведения о построенных зданиях и исследованиях в таблицы не входят.",
        "- Смысл `v[1]`, `v[2]` у ссылок `world state`/`NPC is` в документах игры не описан; значения публикуются без трактовки.",
        "- Итоговые цены продажи и покупки в городах не вычисляются: связь город - культура задана записями, "
        "но остальные множители (отряд-торговец, константы, разброс) не сверены с игрой.",
        "- Для зданий без поселения в `storage.csv` колонка `settlement` пуста, а `location_key` содержит «зона N (поселение не определено)».",
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
) -> dict[str, bytes]:
    """Файлы tables/: имя -> содержимое. Без справочника игры колонки шаблонов и таблицы реакций пусты; это названо в index.md."""
    tables = [
        squad_table(current, locations),
        equipment_table(current, game_index),
        storage_table(current, locations, game_index, zone_settlements(locations, world_ref)),
        item_prices_table(game_index),
        trade_modifiers_table(game_records, game_index),
        unique_npcs_table(current),
        town_overrides_table(current, game_index),
        diplomacy_table(current),
        world_reactions_table(game_records, game_index),
        world_reaction_links_table(current, game_records, game_index),
    ]
    mods = ((current.get("meta") or {}).get("game_data") or {}).get("mods") if game_index else None
    notes = list(game_notes)
    if game_index is None:
        notes.append("Справочник игры недоступен: колонки `tpl_*`, множители, цены (`base_price`, `item_prices.csv`) "
                     "и названия по sid не заполнены.")
    if game_records is None:
        notes.append("Записи реакций и торговли недоступны: `trade_modifiers.csv`, `world_reactions.csv`, "
                     "`world_reaction_links.csv` пусты.")
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
