"""Характеристики предметов: поля экземпляра из сохранения + шаблоны игры + проверенные формулы.

Каждое выводимое поле имеет запись в VERIFICATION: источник, формула и чем подтверждено.
Формулы брони — Kenshi Wiki «Armor Stats» (https://kenshi.fandom.com/wiki/Armor_Stats), оружия —
«Weapons (FCS)», сверены с интерфейсом. Подробности и контрольные значения — docs/item-stats.md.
"""
import math
import struct
from typing import Any

# Грейды из GUI-локализации (main.po, items.h): Prototype, Shoddy, Standard, High, Specialist, Masterwork.
# Уровни грейдов брони — таблица Kenshi Wiki «Armor Stats» (5/20/40/60/80/95). Карточками игры подтверждены
# 40 «Стандартный», 60 «Высокий», 80 «Специальный», 95 «Мастерский»; 5 «Прото» и 20 «Низкий» взяты с вики
# и карточками не подтверждены (UNCONFIRMED_GRADE_LEVELS). Промежуточные уровни грейда не получают:
# порогов между ними источники не дают.
CONFIRMED_GRADES: dict[str, dict[int, str]] = {
    "ARMOUR": {5: "Прото", 20: "Низкий", 40: "Стандартный", 60: "Высокий", 80: "Специальный", 95: "Мастерский"},
    "CROSSBOW": {95: "Мастерский"},
}
UNCONFIRMED_GRADE_LEVELS: dict[str, tuple[int, ...]] = {"ARMOUR": (5, 20)}
# Сочетания, для которых формулы сверены с карточками игры (cards.json ... cards5.json, день 134); calc_* таблиц
# заполняются только для них. Броня: (class, material type) - лёгкий/средний/тяжёлый x кожа/кольчуга/латы;
# ткань (class 0, material 0) и тяжёлые кожа/кольчуга карточек не имеют.
CONFIRMED_ARMOUR_COMBOS: frozenset[tuple[int, int]] = frozenset({(1, 1), (1, 2), (2, 1), (2, 2), (2, 3), (3, 3)})
# Оружие ближнего боя: ('skill category', название производителя en): Катаны 0, Сабли 1, Тяжёлое 3, Рубачи 4, Древковое 8.
CONFIRMED_MELEE_COMBOS: frozenset[tuple[int, str]] = frozenset({
    (0, "Edgewalkers"), (0, "Skeleton Smiths"), (0, "Catun Scrapmaster"),
    (1, "Edgewalkers"), (1, "Cross"), (1, "Skeleton Smiths"),
    (8, "Edgewalkers"), (8, "Cross"),
    (3, "Skeleton Smiths"), (3, "Catun Scrapmaster"),
    (4, "Edgewalkers"), (4, "Ancient"), (4, "Cross")})
# Арбалеты сверены на Зубочистке, Старом балете, Диверсанте, Орлином кресте, Рессоробое; протезы - на ноге разведчика.
CONFIRMED_PROSTHETIC_LIMBS: tuple[str, ...] = ("left_leg", "right_leg")
# Коэффициенты движка по классу и материалу брони — Kenshi Wiki «Armor Stats».
CLASS_CUT = {0: 0.4, 1: 0.8, 2: 0.9, 3: 1.0}
CLASS_BLUNT = {0: 0.03, 1: 0.21, 2: 0.27, 3: 0.3}
MATERIAL_CUT = {0: 0.1, 1: 0.55, 2: 0.6, 3: 0.75}
MATERIAL_BLUNT = {0: 1.0, 1: 4 / 3, 2: 1.0, 3: 2.0}
MATERIAL_HARPOON = {0: 0.0, 1: 0.35, 2: 0.6, 3: 0.9}
MATERIAL_CUT_BONUS = {0: 0, 1: 0, 2: 5, 3: 10}
# (min, max) эффекта при уровне 0 и 100: класс и материал перемножаются, затем умножаются на поле шаблона.
CLASS_EFFECT = {"stealth": {0: (1, 1), 1: (1, 1), 2: (0.9, 0.9), 3: (0, 0.4)},
                "athletics": {0: (1, 1), 1: (1, 1), 2: (0.85, 1), 3: (0.8, 0.95)},
                "combat_speed": {0: (1, 1), 1: (1, 1), 2: (1, 1), 3: (0.9, 1)}}
MATERIAL_EFFECT = {"stealth": {0: (1, 1), 1: (1, 1), 2: (0.3, 0.7), 3: (0.1, 0.8)}}
EFFECT_FIELD = {"stealth": "stealth mult", "athletics": "athletics mult", "combat_speed": "combat speed mult"}
# Слоты (AttachSlot): шлем не влияет на эффекты, атлетику дают только штаны и обувь (Armor Stats).
SLOT_HAT, SLOT_BODY, SLOT_LEGS, SLOT_SHIRT, SLOT_BOOTS = 3, 5, 6, 8, 9
# Вес: кг на 1% покрытия для лат в слотах тела/штанов/обуви; грудь ×1.5; материал и слот — множители.
WEIGHT_PER_PCT = 0.05
WEIGHT_MATERIAL = {0: 0.1, 1: 0.25, 2: 0.9, 3: 1.0}
WEIGHT_SLOT = {SLOT_HAT: 1.25, SLOT_SHIRT: 0.5}
RESIST_CAP = 90.0
# Рубахи: режущее и дробящее ×0.8 (Armor Stats); гарпуны ×0.5 — по интерфейсу (3 рубахи), на вики не описано.
SHIRT_RESIST, SHIRT_HARPOON = 0.8, 0.5
# Порядок перечисления ArmourClass в FCS; 1 и 2 сверены с интерфейсом (см. VERIFICATION).
ARMOUR_CLASS = {0: "cloth", 1: "light", 2: "medium", 3: "heavy"}
# MaterialType из fcs_enums.def.
ARMOUR_MATERIAL = {0: "cloth", 1: "leather", 2: "chain", 3: "plate"}
# WeatherAffecting, коды сверены с карточками: 1 песчаная буря, 2 кислотный дождь, 3 жара (в игре «Опаление»), 4 газ.
WEATHER = {1: "sand_storm", 2: "acid_rain", 3: "heat", 4: "gas"}
# Шаблон хранит коды в 'weather protection0..N' с общим 'weather protection amount'.
WEATHER_FIELD = "weather protection"
CROSSBOW_SPEED_SCALE = 0.23  # 'shot speed' шаблона -> м/с карточки (6 из 6 карточек)
# Поле шаблона брони -> ключ модификатора; значения по умолчанию (1.0 / 0) не выводятся.
# Скрытность, атлетика и скорость боя считаются отдельно (armour_effects).
ARMOUR_MODIFIERS = {
    "ranged skill mult": "ranged_multiplier",
    "dodge mult": "dodge_multiplier", "dexterity mult": "dexterity_multiplier",
    "asassination mult": "assassination_multiplier", "damage output mult": "damage_output_multiplier",
    "fist injury mult": "fist_injury_multiplier",
    "unarmed bonus": "martial_arts_additive", "perception bonus": "perception_additive",
    "combat attk bonus": "melee_attack_additive", "combat def bonus": "melee_defence_additive",
    "combat skill bonus": "combat_skill_additive", "ranged bonus": "ranged_additive",
}
# Поля шаблона, которые интерфейс не показывает (2 образца: Шлем с маской, Шлем самурая);
# выводятся отдельно, без интерпретации как модификатор.
ARMOUR_UNSHOWN = {"combat skill bonus"}
# item function шаблона ITEM -> категория запасов. Названия категорий даны по составу
# предметов с этим кодом в сохранении 2026-10-24 (перечень — docs/item-stats.md).
SUPPLY_CATEGORIES = {1: "medkits", 2: "splints", 3: "food", 15: "raw_food", 12: "robot_repair", 16: "ammo"}
# Категории, у которых 'charges' экземпляра — остаток питательности (у аптечек то же поле значит другое).
NUTRITION_CATEGORIES = {"food", "raw_food"}

VERIFICATION: dict[str, dict[str, Any]] = {
    "weapon.grade": {"status": "verified", "source": "template",
                     "how": "название модели (material sid, MATERIAL_SPECS_WEAPON)", "ui_samples": 4},
    "weapon.damage_cut": {"status": "verified", "source": "calculated",
                          "how": "cut damage multiplier × cut damage mod производителя × (0.3 + 0.017 × level)",
                          "ui_samples": 5, "note": "параметры подобраны по 4 точкам; 5-я (Бердыш, level 55) совпала независимо"},
    "weapon.damage_blunt": {"status": "verified", "source": "calculated",
                            "how": "blunt damage multiplier × blunt damage mod производителя × level / 50",
                            "ui_samples": 3, "note": "параметры подобраны по 2 точкам; 3-я (Бердыш) совпала независимо"},
    "weapon.weight_kg": {"status": "verified", "source": "calculated",
                         "how": "max(weight kg, damage_blunt × 40 × weapon inventory weight mult × weight mult) — "
                                "Weapons (FCS), Kenshi Wiki",
                         "ui_samples": 5, "note": "интерфейс показывает целую часть (6.6 → 6). Множитель производителя, "
                                                  "указанный на вики, интерфейсом не подтверждён (Падающее солнце: "
                                                  "14 кг, с множителем было бы 17) и не применяется. Не подтверждено: "
                                                  "Падающее солнце L60 - карточка 23, формула 24.0; Боевой тесак Древних - "
                                                  "карточка 5, формула 6 (по 1 образцу). Гипотеза, не применяется: "
                                                  "melee_weight со 'weight mod' производителя (1 образец)"},
    "weapon.armour_penetration/attack_mod/indoors_mod/bleed_mult/animal_damage_mult": {
        "status": "verified", "source": "template", "how": "поля шаблона WEAPON", "ui_samples": 1,
        "note": "Бердыш: пробитие +30%, бонус к атаке +4, штраф в здании −4, кровопотеря 1.00x, урон по зверям +25%"},
    "weapon.defence_mod/human_damage_mult/robot_damage_mult": {"status": "template", "source": "template",
                                                               "how": "поля шаблона WEAPON", "ui_samples": 0},
    "weapon.slot": {"status": "verified", "how": "weapon_back = «Оружие I», weapon_hip = «Оружие II»", "ui_samples": 1},
    "crossbow.reload_s": {"status": "verified", "source": "calculated",
                          "how": "линейно между 'reload time min/max' (худшее) и '... 1' (лучшее), q = level/100",
                          "ui_samples": 1},
    "crossbow.spread_deg": {"status": "verified", "source": "calculated",
                            "how": "между 'accuracy deviation at 0 skill' и '... 1', q = level/100", "ui_samples": 1},
    "crossbow.range_m": {"status": "verified", "source": "calculated",
                         "how": "между 'range' и 'range 1', q = level/100, ÷10", "ui_samples": 1},
    "crossbow.required_skill": {"status": "verified", "source": "template",
                                "how": "'accuracy perfect skill'", "ui_samples": 1},
    "crossbow.damage": {"status": "verified", "source": "calculated",
                        "how": "между 'pierce damage min/max 0' и '... 1' × между 'bow damage 1' и '99' (CONSTANTS); "
                               "нижняя граница усекается, верхняя округляется до целого",
                        "ui_samples": 6, "note": "63.21-100.62 → 63-101"},
    "crossbow.grade/armour.grade": {"status": "verified_points", "source": "save level",
                                    "how": "уровни грейдов брони — Armor Stats (5/20/40/60/80/95); карточками подтверждены 40, 60, 80, "
                                           "95, а 5 «Прото» и 20 «Низкий» не подтверждены; арбалеты — только 95; иначе null",
                                    "points": {"ARMOUR": CONFIRMED_GRADES["ARMOUR"],
                                               "CROSSBOW": CONFIRMED_GRADES["CROSSBOW"]}},
    "armour.class": {"status": "verified", "source": "template",
                     "how": "поле 'class' (ArmourClass): light, medium, heavy", "ui_samples": 19},
    "armour.material": {"status": "verified", "source": "template", "how": "поле 'material type'", "ui_samples": 18},
    "armour.coverage": {"status": "verified", "source": "template",
                        "how": "refs 'part coverage', val0 % / 100", "ui_samples": 19},
    "armour.cut_efficiency": {"status": "verified", "source": "calculated",
                              "how": "1 − 'cut into stun'", "ui_samples": 19},
    "armour.skill_modifiers": {"status": "verified", "source": "template",
                               "how": "множители и бонусы шаблона ARMOUR, отличные от нейтральных; "
                                      "fist_injury_multiplier 0.5 = «Защита кулаков 50%»",
                               "ui_samples": 38},
    "armour.skill_modifiers.stealth/athletics/combat_speed": {
        "status": "verified", "source": "calculated",
        "how": "поле шаблона × (min + (max − min) × E/100) класса × то же для материала, E = level + level bonus "
               "(Armor Stats); у шлема - только множитель шаблона без класса и материала; атлетику дают только "
               "штаны и обувь; отрицательный множитель шаблона — эффекта нет",
        "ui_samples": 17,
        "note": "интерфейс показывает 2 знака: 0.0096 → 0.01, 0.0372 → 0.04"},
    "armour.resistances": {
        "status": "verified", "source": "calculated",
        "how": "cut% = E × (класс_cut × материал_cut + cut def bonus) + бонус_материала × класс_cut; "
               "blunt% = E × (класс_blunt × материал_blunt + blunt def bonus); harpoon = E × float32(материал_harpoon) × "
               "pierce def mult; рубахи: cut/blunt × 0.8, harpoon × 0.5; потолок 90% (Armor Stats); карточка усекает "
               "неокруглённое значение",
        "ui_samples": 54,
        "note": "18 предметов/уровней × 3 типа совпали с интерфейсом. Интерфейс показывает целую часть, а точно "
                "целое значение иногда на 1 меньше (0.9 × 40 = 36 → «35»). Множитель гарпунов для рубах 0.5 "
                "установлен по интерфейсу; вики указывает 0.8 для всех сопротивлений рубах"},
    "armour.weight_kg": {
        "status": "verified", "source": "calculated",
        "how": "Σ покрытие% × 0.05 × материал (латы 1, кольчуга 0.9, кожа 0.25, ткань 0.1) × слот (шлем 1.25, "
               "рубаха 0.5) × 1.5 для груди; не меньше 1 кг (Armor Stats)",
        "ui_samples": 16, "note": "интерфейс показывает целую часть (5.625 → 5)"},
    "item.stolen": {"status": "verified", "source": "save",
                    "how": "владелец предмета — другой персонаж (поле ownedbyTYPE = 1)", "ui_samples": 16,
                    "note": "4 краденых и 12 некраденых совпали с пометкой «Украдено»; владельцы — NPC, "
                            "ни один не из отряда игрока"},
    "armour.template_unshown": {"status": "template", "source": "template",
                                "how": "поля шаблона, которые интерфейс не показывает: 'combat skill bonus' (2 образца), "
                                       "отрицательный множитель (stealth mult −1 у Чёрной куртки с латами — в игре "
                                       "модификатора скрытности нет)", "ui_samples": 3},
    "armour.weather_protection": {"status": "verified", "source": "template",
                                  "how": "'weather protection amount' и коды 'weather protection0..N' (1 = песчаные бури, "
                                         "2 = кислотный дождь, 3 = жара, 4 = газ); другие коды - code_N", "ui_samples": 12,
                                  "note": "индекс gamedb хранит только 'weather protection0': у шаблонов с несколькими "
                                          "кодами выводится первый (STAT_FIELDS и INDEX_VERSION не менялись - отложено)"},
    "crossbow.shot_speed": {"status": "verified", "source": "calculated",
                            "how": "round(между 'shot speed' и 'shot speed 1', q = level/100, × 0.23) - м/с карточки",
                            "ui_samples": 6, "note": "Орлиный крест L95: 795 × 0.23 = 182.85 → 183"},
    "crossbow.card_value": {"status": "verified", "source": "calculated",
                            "how": "trunc('value' + ('value 1' − 'value') × q²), q = level/100 - цена карточки",
                            "ui_samples": 6, "note": "Орлиный крест L95: 30928; «Украдено» цену арбалета не меняет"},
    "weapon.armour.price": {"status": "unavailable",
                            "how": "цена карточки оружия и брони: закон не установлен, не публикуется. Деревянные "
                                   "сандали: карточка 50, формула 250 - не подтверждено"},
    "armour.athletics_hide_threshold": {"status": "unverified",
                                        "how": "порог скрытия атлетики на карточке не установлен: множители 0.9681/0.9775/"
                                               "0.995 карточка не показывает, значения публикуются без порога"},
    "armour.resistances_boundary_59": {"status": "unverified",
                                       "how": "у карточек Высокого уровня при целых произведениях (граница 59.x) blunt "
                                              "карточки на 1 ниже расчёта; причина не установлена"},
    "carried_weight_kg": {"status": "verified", "source": "calculated",
                          "how": "Σ вес предметов персонажа: экипировка, пояс, рюкзак и инвентарь — полностью, содержимое "
                                 "надетого рюкзака × 'encumbrance effect' рюкзака; протезы не учитываются. Вес: броня и "
                                 "оружие — формулы выше, арбалет — 'weight kg' × weapon inventory weight mult, прочее — "
                                 "'weight kg' шаблона",
                          "ui_samples": 5,
                          "note": "«Общий вес» показывает целую часть: 36.26 → 36, 42.88 → 42. Арбалет — 1 образец "
                                  "(Рейнджер, 7 × 0.5); у животных не выводится — не сверено"},
    "item.nutrition": {"status": "unverified", "source": "save",
                       "how": "Σ 'charges' экземпляра × quantity по еде и сырой еде (питательность, «пит»); у "
                              "надкушенного предмета — остаток",
                       "ui_samples": 0,
                       "note": "у целых предметов charges = 'charges' шаблона × 'food quality mult' (0.5, CONSTANTS) — "
                               "совпало у 5 видов еды в сохранении дня 83; с подсказкой «Питательность» не сверено. "
                               "Темп голода (раса, сон, перегруз, настройка «Время голода») не выводится"},
    "overload_pct": {"status": "unavailable",
                     "how": "грузоподъёмность = 15 + эффективная Сила (5 из 5 по интерфейсу), а эффективной Силы "
                            "(травмы, протезы, снаряжение) в сохранении нет; кандидат (вес / грузоподъёмность − 1.2) / 0.6 "
                            "сходится на 2 образцах «Лёгкой ноши», при «Тяжело» — нет"},
}


def lerp(a: float, b: float, q: float) -> float:
    return a + (b - a) * q


def _f32(x: float) -> float:
    """Значение, как его хранит движок (float32): 0.35 -> 0.3499999940395355."""
    return struct.unpack("f", struct.pack("f", x))[0]


def _round_half_up(x: float) -> int:
    return math.floor(x + 1e-9 + 0.5)


def melee_damage(t: dict[str, Any], manuf: dict[str, Any] | None, level: int) -> tuple[float, float]:
    m = manuf or {}
    cut = t.get("cut damage multiplier", 0.0) * m.get("cut damage mod", 1.0) * (0.3 + 0.017 * level)
    blunt = t.get("blunt damage multiplier", 0.0) * m.get("blunt damage mod", 1.0) * level / 50
    return cut, blunt


def melee_weight(t: dict[str, Any], blunt: float, inv_weight_mult: float) -> float:
    return max(t.get("weight kg", 0.0), blunt * 40 * inv_weight_mult * t.get("weight mult", 1.0))


def crossbow_stats(t: dict[str, Any], level: int, const: dict[str, Any]) -> dict[str, Any]:
    q = level / 100
    bow = lerp(const.get("bow damage 1", 1.0), const.get("bow damage 99", 1.0), q)
    out: dict[str, Any] = {}
    if "pierce damage min 0" in t and "pierce damage max 0" in t:
        lo = lerp(t["pierce damage min 0"], t.get("pierce damage min 1", t["pierce damage min 0"]), q) * bow
        hi = lerp(t["pierce damage max 0"], t.get("pierce damage max 1", t["pierce damage max 0"]), q) * bow
        out["damage"] = [math.floor(lo + 1e-9), _round_half_up(hi)]  # карточка: min усекает, max округляет
    if "reload time min" in t and "reload time max" in t:
        out["reload_s"] = [round(lerp(t["reload time min"], t.get("reload time min 1", t["reload time min"]), q), 2),
                           round(lerp(t["reload time max"], t.get("reload time max 1", t["reload time max"]), q), 2)]
    if "accuracy deviation at 0 skill" in t:
        a0 = t["accuracy deviation at 0 skill"]
        out["spread_deg"] = round(lerp(a0, t.get("accuracy deviation at 0 skill 1", a0), q), 2)
    if "range" in t:
        out["range_m"] = round(lerp(t["range"], t.get("range 1", t["range"]), q) / 10, 1)
    if "accuracy perfect skill" in t:
        out["required_skill"] = t["accuracy perfect skill"]
    if "shot speed" in t:
        speed = lerp(t["shot speed"], t.get("shot speed 1", t["shot speed"]), q)
        out["shot_speed_ms"] = _round_half_up(speed * CROSSBOW_SPEED_SCALE)
    if "value" in t:
        out["card_value"] = math.floor(1e-9 + lerp(t["value"], t.get("value 1", t["value"]), q * q))  # q², не q
    return out


def grade_for(type_name: str, level: int | None) -> str | None:
    if level is None:
        return None
    return CONFIRMED_GRADES.get(type_name, {}).get(level)


def _lin(pair: tuple[float, float], e: float) -> float:
    lo, hi = pair
    return lo + (hi - lo) * e / 100


def armour_resistances(t: dict[str, Any], e: float) -> dict[str, float] | None:
    """Сопротивления при эффективном уровне e (level + level bonus); cut/blunt в процентах, harpoon в единицах."""
    c, m = t.get("class"), t.get("material type")
    if c not in CLASS_CUT or m not in MATERIAL_CUT:
        return None
    cut = e * (CLASS_CUT[c] * MATERIAL_CUT[m] + t.get("cut def bonus", 0.0)) + MATERIAL_CUT_BONUS[m] * CLASS_CUT[c]
    blunt = e * (CLASS_BLUNT[c] * MATERIAL_BLUNT[m] + t.get("blunt def bonus", 0.0))
    harpoon = e * _f32(MATERIAL_HARPOON[m]) * t.get("pierce def mult", 1.0)
    if t.get("slot") == SLOT_SHIRT:
        cut, blunt, harpoon = cut * SHIRT_RESIST, blunt * SHIRT_RESIST, harpoon * SHIRT_HARPOON
    # Без промежуточного округления: карточка усекает целую часть неокруглённого значения.
    return {"cut_pct": min(cut, RESIST_CAP), "blunt_pct": min(blunt, RESIST_CAP), "harpoon": harpoon}


def armour_effects(t: dict[str, Any], e: float) -> dict[str, float]:
    """Скрытность, атлетика, скорость боя с поправкой по классу, материалу и уровню; нейтральные не выводятся."""
    slot, c, m = t.get("slot"), t.get("class"), t.get("material type")
    out: dict[str, float] = {}
    hat = slot == SLOT_HAT  # шлем: множители шаблона без класса и материала (Маска Болотного Ниндзя: 1.1)
    if not hat and c not in CLASS_CUT:
        return out
    for name, field in EFFECT_FIELD.items():
        if name == "athletics" and slot not in (SLOT_LEGS, SLOT_BOOTS):
            continue
        base = t.get(field, 1.0)
        if base < 0:  # Чёрная куртка с латами: −1 в шаблоне, в игре эффекта нет
            continue
        v = base if hat else base * _lin(CLASS_EFFECT[name][c], e) * _lin(MATERIAL_EFFECT.get(name, {}).get(m, (1, 1)), e)
        if abs(v - 1.0) > 1e-6:
            out[f"{name}_multiplier"] = round(v, 4)
    return out


def armour_weight_raw(t: dict[str, Any], is_chest: Any) -> float | None:
    """Вес брони без округления — для суммы веса персонажа."""
    cov = t.get("refs:part coverage") or {}
    m = t.get("material type")
    if not cov:
        return t.get("weight kg")
    if m not in WEIGHT_MATERIAL:
        return None
    k = WEIGHT_PER_PCT * WEIGHT_MATERIAL[m] * WEIGHT_SLOT.get(t.get("slot"), 1.0)
    w = sum(v[0] * k * (1.5 if is_chest(sid) else 1.0) for sid, v in cov.items())
    return max(w, 1.0)


def armour_weight(t: dict[str, Any], is_chest: Any) -> float | None:
    w = armour_weight_raw(t, is_chest)
    return None if w is None else round(w, 2)


def item_weight(type_name: str | None, t: dict[str, Any], level: int | None, manuf: dict[str, Any] | None,
                const: dict[str, Any], is_chest: Any) -> float | None:
    """Вес одного предмета, как его учитывает «Общий вес» (docs/item-stats.md, «Вес персонажа»); None — неизвестен."""
    if type_name == "ARMOUR":
        return armour_weight_raw(t, is_chest)
    if type_name == "WEAPON":
        if level is None:
            return None
        return melee_weight(t, melee_damage(t, manuf, level)[1], const.get("weapon inventory weight mult", 0.5))
    if type_name == "CROSSBOW":
        # Поле 'weight' шаблона (5 у «Рейнджера») не совпало с интерфейсом; 'weight kg' × 0.5 — совпало.
        w = t.get("weight kg")
        return None if w is None else w * const.get("weapon inventory weight mult", 0.5)
    return t.get("weight kg")


def carried_weight(rows: list[tuple[float | None, int, float]]) -> float | None:
    """Σ вес × количество × коэффициент места (содержимое надетого рюкзака — 'encumbrance effect' рюкзака)."""
    if any(w is None for w, _q, _m in rows):
        return None
    return sum(w * q * m for w, q, m in rows)


def armour_view(t: dict[str, Any], level: int | None, part_name: Any, is_chest: Any = lambda sid: False) -> dict[str, Any]:
    """Броня: поля шаблона и формулы движка (Armor Stats); part_name(sid) -> название части тела."""
    out: dict[str, Any] = {"grade": grade_for("ARMOUR", level)}
    if "class" in t:
        out["class"] = ARMOUR_CLASS.get(t["class"], f"code_{t['class']}")
    if "material type" in t:
        out["material"] = ARMOUR_MATERIAL.get(t["material type"], f"code_{t['material type']}")
    if "cut into stun" in t:
        out["cut_efficiency"] = round(1 - t["cut into stun"], 2)
    cov = t.get("refs:part coverage") or {}
    if cov:
        out["coverage"] = {part_name(sid): round(v[0] / 100, 2) for sid, v in cov.items()}
    mods, unshown = {}, {}
    for field, key in ARMOUR_MODIFIERS.items():
        v = t.get(field)
        is_mult = key.endswith("multiplier")
        if v is None or abs(v - (1.0 if is_mult else 0)) <= 1e-6:
            continue
        if field in ARMOUR_UNSHOWN or (is_mult and v < 0):
            unshown[field] = round(v, 2)
        else:
            mods[key] = round(v, 2)
    if t.get("stealth mult", 1.0) < 0:
        unshown["stealth mult"] = round(t["stealth mult"], 2)
    e = None if level is None else level + t.get("level bonus", 0)
    if e is not None:
        mods |= armour_effects(t, e)
    if mods:
        out["skill_modifiers"] = mods
    if unshown:
        out["template_unshown"] = unshown
    if e is not None:
        res = armour_resistances(t, e)
        if res:
            # 8 знаков: карточка усекает неокруглённое, 71.999998 (harpoon шлема) не должно стать 72.
            out["resistances"] = {k: round(v, 8) for k, v in res.items()}
    w = armour_weight(t, is_chest)
    if w is not None:
        out["weight_kg"] = w
    wp = t.get("weather protection amount")
    if wp:
        codes = [t[k] for k in sorted((k for k in t if k.startswith(WEATHER_FIELD) and k[len(WEATHER_FIELD):].isdigit()),
                                      key=lambda k: int(k[len(WEATHER_FIELD):]))]
        out["weather_protection"] = {WEATHER.get(code, f"code_{code}"): round(wp, 2) for code in codes}
    return out


def weapon_view(t: dict[str, Any], manuf: dict[str, Any] | None, level: int | None,
                const: dict[str, Any]) -> dict[str, Any]:
    if level is None:
        return {}
    cut, blunt = melee_damage(t, manuf, level)
    out = {"damage_cut": round(cut, 2), "damage_blunt": round(blunt, 2),
           "weight_kg": round(melee_weight(t, blunt, const.get("weapon inventory weight mult", 0.5)), 1)}
    for field, key in (("armour penetration", "armour_penetration"), ("attack mod", "attack_mod"),
                       ("defence mod", "defence_mod")):
        if field in t:
            out[key] = round(t[field], 2)
    # Показываются только отличные от нейтральных — как в подсказке игры.
    for field, key, neutral in (("indoors mod", "indoors_mod", 0), ("bleed mult", "bleed_mult", 1.0),
                                ("animal damage mult", "animal_damage_mult", 1.0),
                                ("human damage mult", "human_damage_mult", 1.0),
                                ("robot damage mult", "robot_damage_mult", 1.0)):
        v = t.get(field)
        if v is not None and abs(v - neutral) > 1e-6:
            out[key] = round(v, 2)
            if field.endswith("damage mult") and field != "bleed mult":
                # Карточка усекает неокруглённый множитель: 0.61 из float32 даёт -39, не -40.
                out["vs_" + {"animal": "beasts", "human": "humans", "robot": "robots"}[field.split()[0]] + "_pct"] = \
                    math.trunc((v - 1) * 100)
    return out
