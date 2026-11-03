"""Расчетные физические и боевые показатели персонажей (P0 #1, #2, #7).

Разделение проверенных данных сохранения и расчетных формул движка:
все вычисляемые характеристики помечаются статусом 'unverified_engine_formula'.
"""
from __future__ import annotations

import math
from typing import Any

from tools.itemstats import ARMOUR_MODIFIERS, EFFECT_FIELD


def calculate_character_physics(char: dict[str, Any]) -> dict[str, Any]:
    """Расчет грузоподъемности, перегруза и скорости бега.

    Статус: unverified_engine_formula (Kenshi 1.0.xx).
    """
    attr = char.get("attributes_base") or {}
    strength = float(attr.get("Strength") or 0.0)
    skills = char.get("skills_base") or {}
    athletics = float(skills.get("Athletics") or 0.0)

    # Вес груза: из carried_weight_kg либо приближенный суммарный вес экипировки и инвентаря
    carried = char.get("carried_weight_kg")
    if carried is None:
        weight_sum = 0.0
        # Оружие
        for w in char.get("weapons", []):
            weight_sum += float(w.get("weight_kg") or w.get("weight") or 2.0)
        # Броня
        for a in (char.get("armour") or {}).values():
            if isinstance(a, dict):
                weight_sum += float(a.get("weight_kg") or a.get("weight") or 2.0)
        # Инвентарь
        for it in char.get("inventory", []):
            weight_sum += float(it.get("quantity", 1)) * float(it.get("weight_kg") or 0.5)
        # Рюкзак
        for it in (char.get("backpack") or {}).get("contents", []):
            weight_sum += float(it.get("quantity", 1)) * float(it.get("weight_kg") or 0.5)
        carried = weight_sum

    carried_kg = max(0.0, round(float(carried), 2))

    # Базовая грузоподъемность: W_cap = Strength * 0.5 (кг без перегруза)
    # При Strength=0 минимальная емкость 1.0 кг для исключения деления на ноль.
    weight_cap = max(1.0, round(strength * 0.5, 2))

    # Процент перегруза
    if carried_kg > weight_cap:
        encumbrance_pct = round(((carried_kg - weight_cap) / weight_cap) * 100.0, 1)
    else:
        encumbrance_pct = 0.0

    # Оценка скорости бега (мили в час, mph):
    # Базовая скорость человека/скелета: ~14 mph + 0.25 * Athletics
    base_speed = 14.0 + (0.25 * athletics)

    # Множители скорости протезов ног
    prosthetics_mult = 1.0
    for p in char.get("prosthetics", []):
        limb = str(p.get("limb", ""))
        if "leg" in limb:
            # Если у протеза ноги есть athletics_mult, учитываем его
            mult = p.get("athletics_mult") or p.get("athletics")
            if mult and isinstance(mult, (int, float)):
                prosthetics_mult *= float(mult)

    # Штраф от перегруза: каждые 10% перегруза снижают скорость примерно на 5-8%
    encumbrance_factor = max(0.15, 1.0 - (encumbrance_pct / 100.0 * 0.7))

    speed_est = round(base_speed * prosthetics_mult * encumbrance_factor, 1)

    return {
        "status": "unverified_engine_formula",
        "carried_weight_kg": carried_kg,
        "weight_capacity_kg": weight_cap,
        "encumbrance_pct": encumbrance_pct,
        "speed_run_mph_est": speed_est,
        "formula_notes": (
            "Расчетные физические показатели: грузоподъемность W_cap=Strength*0.5; "
            "перегруз (Weight-W_cap)/W_cap*100%; скорость от Athletics, ног и перегруза. "
            "Статус unverified_engine_formula."
        ),
    }


def calculate_character_combat(char: dict[str, Any]) -> dict[str, Any]:
    """Расчет требований оружия к силе, штрафов брони и покрытия частей тела.

    Статус: unverified_engine_formula (Kenshi 1.0.xx).
    """
    attr = char.get("attributes_base") or {}
    strength = float(attr.get("Strength") or 0.0)

    # 1. Требования оружия к Силе
    weapon_reqs: list[dict[str, Any]] = []
    weapons = char.get("weapons") or []
    for w in weapons:
        if not isinstance(w, dict):
            continue
        w_name = w.get("name") or w.get("sid", "Оружие")
        slot = w.get("slot", "weapon")
        # Вес оружия (кг)
        w_weight = float(w.get("weight_kg") or w.get("weight") or 3.0)
        # Дробящий урон
        damage_blunt = float(w.get("damage_blunt") or w.get("blunt") or 0.0)

        # Формула Kenshi: Req = max(2 * weight_kg, 40 * damage_blunt)
        req_weight = round(2.0 * w_weight, 1)
        req_blunt = round(40.0 * damage_blunt, 1)
        strength_req = max(req_weight, req_blunt)

        deficit = max(0.0, round(strength_req - strength, 1))
        # При дефиците силы штраф к скорости атаки пропорционален нехватке
        speed_penalty_pct = min(60.0, round(deficit * 2.0, 1)) if deficit > 0 else 0.0

        weapon_reqs.append({
            "slot": slot,
            "name": w_name,
            "weight_kg": round(w_weight, 2),
            "damage_blunt": round(damage_blunt, 2),
            "strength_required": strength_req,
            "strength_deficit": deficit,
            "speed_penalty_pct": speed_penalty_pct,
        })

    # 2. Сводные штрафы брони
    armour = char.get("armour") or {}
    stealth_mult = 1.0
    athletics_mult = 1.0
    combat_speed_mult = 1.0
    dodge_mult = 1.0

    # 3. Покрытие частей тела: head, chest, stomach, left_arm, right_arm, left_leg, right_leg
    zones = ["head", "chest", "stomach", "left_arm", "right_arm", "left_leg", "right_leg"]
    body_coverage: dict[str, dict[str, Any]] = {
        z: {"coverage_pct": 0, "cut_resist_pct": 0.0, "blunt_resist_pct": 0.0, "layers": []}
        for z in zones
    }

    slot_to_zones: dict[str, list[str]] = {
        "head": ["head"],
        "hat": ["head"],
        "chest": ["chest", "stomach", "left_arm", "right_arm"],
        "body": ["chest", "stomach", "left_arm", "right_arm"],
        "shirt": ["chest", "stomach", "left_arm", "right_arm"],
        "legs": ["stomach", "left_leg", "right_leg"],
        "boots": ["left_leg", "right_leg"],
    }

    for slot, item in armour.items():
        if not isinstance(item, dict):
            continue
        # Штрафы брони (если указаны в параметрах предмета)
        if "stealth_mult" in item:
            stealth_mult *= float(item["stealth_mult"])
        if "athletics_mult" in item:
            athletics_mult *= float(item["athletics_mult"])
        if "combat_speed_mult" in item:
            combat_speed_mult *= float(item["combat_speed_mult"])
        if "dodge_mult" in item:
            dodge_mult *= float(item["dodge_mult"])

        # Распределение защиты по зонам
        covering = slot_to_zones.get(slot.lower(), [])
        cut_res = float(item.get("cut_resist") or item.get("cut") or 0.0)
        blunt_res = float(item.get("blunt_resist") or item.get("blunt") or 0.0)
        cov_val = item.get("coverage")

        for z in covering:
            body_coverage[z]["layers"].append(item.get("name") or item.get("sid", slot))
            if isinstance(cov_val, dict):
                zone_cov = cov_val.get(z) or cov_val.get(z.lower()) or 100
                try:
                    coverage = int(zone_cov)
                except (ValueError, TypeError):
                    coverage = 100
            else:
                try:
                    coverage = int(cov_val or 100)
                except (ValueError, TypeError):
                    coverage = 100
            body_coverage[z]["coverage_pct"] = max(body_coverage[z]["coverage_pct"], coverage)
            # При послойной броне сопротивления приближенно суммируются с затуханием
            body_coverage[z]["cut_resist_pct"] = round(min(90.0, body_coverage[z]["cut_resist_pct"] + cut_res), 1)
            body_coverage[z]["blunt_resist_pct"] = round(min(90.0, body_coverage[z]["blunt_resist_pct"] + blunt_res), 1)

    return {
        "status": "unverified_engine_formula",
        "weapon_requirements": weapon_reqs,
        "armour_penalties": {
            "stealth_mult": round(stealth_mult, 2),
            "athletics_mult": round(athletics_mult, 2),
            "combat_speed_mult": round(combat_speed_mult, 2),
            "dodge_mult": round(dodge_mult, 2),
        },
        "body_coverage": body_coverage,
        "formula_notes": (
            "Расчетные боевые показатели: требования оружия Req=max(2*W, 40*Blunt); "
            "штрафы брони и послойное покрытие частей тела. "
            "Статус unverified_engine_formula."
        ),
    }
