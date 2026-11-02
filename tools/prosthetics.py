"""Протезы и потерянные конечности: запись здоровья 57 (int 'limbs', refs 'limbs') и шаблон LIMB_REPLACEMENT.

Где лежат данные и что подтверждено — docs/save-format.md, раздел «Протезы»; формат блока — README.md.
"""
import math
from typing import Any

# Номер конечности в битах 'limbs' и в refs 'limbs' (v[0]); совпадает с полем 'slot' шаблона − 50.
LIMBS = ["left_arm", "right_arm", "left_leg", "right_leg"]
# Английские имена частей тела (записи *-gamedata.quack) — по ним находится часть в списке здоровья.
LIMB_PART_EN = {"left_arm": "left arm", "right_arm": "right arm", "left_leg": "left leg", "right_leg": "right leg"}
LIMB_SLOT_BASE = 50
# 2 бита на конечность: 0 — своя, 1 — потеряна, 2 — протез. Значение 3 не встречалось ни разу.
STATE_LOST, STATE_PROSTHETIC = 1, 2
# Поля шаблона с парой «худшее качество / лучшее качество» ('<поле>' и '<поле> 1') -> ключ выгрузки.
RANGE_FIELDS = {"HP": "hp", "strength mult": "strength_mult", "dexterity mult": "dexterity_mult",
                "athletics mult": "athletics_mult", "swimming mult": "swimming_mult",
                "stealth mult": "stealth_mult", "thievery mult": "thievery_mult", "ranged mult": "ranged_mult",
                "unarmed damage bonus": "unarmed_damage_bonus"}
# Грейды по уровню, подтверждённые подсказкой протеза (набор 5, docs/item-stats.md); другие уровни — null.
CONFIRMED_GRADES = {80: "Специальный", 95: "Мастерский"}
# «ОЗ Макс» округляется до 5 (наблюдение по 1132 протезам и 3 подсказкам).
HP_STEP = 5
# Поля без пары; нейтральное значение (1.0) не выводится.
SINGLE_FIELDS = {"combat speed mult": "combat_speed_mult", "encumbrance mult": "encumbrance_mult",
                 "overall mult": "overall_mult"}

# Статусы блока prosthetics (попадают в meta.verification); основание — docs/save-format.md, «Протезы».
VERIFICATION: dict[str, dict[str, Any]] = {
    "prosthetics.limb": {"status": "verified", "source": "save",
                         "how": "refs 'limbs' записи 57 -> предмет 42; конечность = v[0] = 'slot' шаблона − 50 "
                                "(1132 из 1132 протезов в 62 сохранениях); 2 бита на конечность в int 'limbs' = 2",
                         "ui_samples": 1, "note": "протез левой руки Хот Лона — синяя полоса в интерфейсе"},
    "prosthetics.model/level": {"status": "verified", "source": "save",
                                "how": "'base data sid' (шаблон LIMB_REPLACEMENT) и 'level' предмета", "ui_samples": 3,
                                "note": "названия моделей Хот Лона, Флика и Бипа совпали с подсказкой"},
    "prosthetics.grade": {"status": "verified_points", "source": "save level",
                          "how": "level 80 = «Специальный», 95 = «Мастерский» (подсказки 3 протезов); иначе null",
                          "points": CONFIRMED_GRADES},
    "prosthetics.hp_max": {"status": "verified", "source": "calculated",
                           "how": "HP + (HP 1 − HP) × level/100, округление до 5 (половина — вверх)", "ui_samples": 3,
                           "note": "«ОЗ Макс» 245, 145, 85 совпали; в сохранениях flesh + wear без перевязки равны "
                                   "этому значению у большинства из 1132 протезов (у остальных — ниже, повреждения)"},
    "prosthetics.template_range": {"status": "template", "source": "template",
                                   "how": "[поле, поле 1] шаблона — значения при худшем и лучшем качестве",
                                   "ui_samples": 0,
                                   "note": "значения при уровне предмета не выводятся: линейная интерполяция по "
                                           "level/100 с отбрасыванием дробной части совпала с подсказкой в 11 из 13 "
                                           "показанных значений, но Сила и Ловкость КЛР (95) в подсказке +10% при "
                                           "расчёте +8,25%; часть ненейтральных полей подсказка не показывает"},
    "prosthetics.template": {"status": "template", "source": "template",
                             "how": "combat speed / encumbrance / overall mult шаблона", "ui_samples": 0},
    "prosthetics.template.weight_kg": {"status": "verified", "source": "template", "how": "'weight kg' шаблона",
                                       "ui_samples": 3, "note": "12, 10, 4 кг — как в подсказке"},
    "lost_limbs": {"status": "partially_verified", "source": "save",
                   "how": "2 бита конечности в int 'limbs' = 1, ссылки на протез нет",
                   "note": "у Бипа левая рука с 5 HP в сохранениях дней 71–74 (до 00:31), в quicksave дня 74 на ней протез; "
                           "с интерфейсом не сверено"},
}


def limb_states(mask: int) -> dict[str, int]:
    """Ненулевые состояния конечностей из int 'limbs' записи 57."""
    return {name: (mask >> (2 * i)) & 3 for i, name in enumerate(LIMBS) if (mask >> (2 * i)) & 3}


def read_limbs(med: dict[str, Any] | None, by: dict[str, Any], warnings: list[str],
               who: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Установленные протезы (конечность -> запись предмета 42) и потерянные конечности без протеза."""
    if med is None:
        return {}, []
    states = limb_states(med["int"].get("limbs", 0))
    items: dict[str, dict[str, Any]] = {}
    for ref in med["refs"].get("limbs", []):
        it = by.get(ref["sid"])
        idx = ref["v"][0] if ref.get("v") else None
        if it is None or it["type"] != 42 or not isinstance(idx, int) or not 0 <= idx < len(LIMBS):
            warnings.append(f"'{who}': ссылка на протез {ref['sid']} не разобрана")
            continue
        items[LIMBS[idx]] = it
    for limb, st in states.items():
        if st == STATE_PROSTHETIC and limb not in items:
            warnings.append(f"'{who}': {limb} отмечена протезом, но ссылки на предмет нет")
        elif st not in (STATE_LOST, STATE_PROSTHETIC):
            warnings.append(f"'{who}': неизвестное состояние конечности {limb} = {st}")
    for limb in items:
        if states.get(limb) != STATE_PROSTHETIC:
            warnings.append(f"'{who}': протез на {limb} без отметки в 'limbs'")
    lost = [limb for limb in LIMBS if states.get(limb) == STATE_LOST and limb not in items]
    return items, lost


def hp_max(t: dict[str, Any], level: int | None) -> int | None:
    """«ОЗ Макс» протеза при уровне предмета."""
    lo, hi = t.get("HP"), t.get("HP 1")
    if level is None or lo is None or hi is None:
        return None
    return int(math.floor((lo + (hi - lo) * level / 100) / HP_STEP + 0.5) * HP_STEP)


def template_view(t: dict[str, Any]) -> dict[str, Any]:
    """Поля шаблона протеза: пары [худшее, лучшее] без нейтральных и одиночные поля."""
    rng = {}
    for field, key in RANGE_FIELDS.items():
        lo, hi = t.get(field), t.get(field + " 1")
        if lo is None and hi is None:
            continue
        lo = hi if lo is None else lo
        hi = lo if hi is None else hi
        neutral = 0 if key in ("hp", "unarmed_damage_bonus") else 1.0
        if key == "hp" or abs(lo - neutral) > 1e-6 or abs(hi - neutral) > 1e-6:
            rng[key] = [round(lo, 2), round(hi, 2)]
    out: dict[str, Any] = {"template_range": rng} if rng else {}
    single = {key: round(t[f], 2) for f, key in SINGLE_FIELDS.items() if f in t and abs(t[f] - 1.0) > 1e-6}
    if "weight kg" in t:
        single["weight_kg"] = round(t["weight kg"], 2)
    if single:
        out["template"] = single
    return out
