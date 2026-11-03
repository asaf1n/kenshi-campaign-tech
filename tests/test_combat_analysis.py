"""Тесты расчетных физических и боевых показателей персонажей (P0 #1, #2, #7)."""
from __future__ import annotations

import unittest
from typing import Any

from tools.combat_analysis import calculate_character_combat, calculate_character_physics
from tools.rendering import render_squad


class CombatAnalysisTests(unittest.TestCase):
    def test_physics_calculations_and_encumbrance(self) -> None:
        """Расчет грузоподъемности, перегруза и скорости бега."""
        char = {
            "id": "ps:1",
            "name": "Флик",
            "attributes_base": {"Strength": 40.0},
            "skills_base": {"Athletics": 60.0},
            "carried_weight_kg": 10.0,
            "prosthetics": [],
        }
        phys = calculate_character_physics(char)
        self.assertEqual(phys["status"], "unverified_engine_formula")
        self.assertEqual(phys["carried_weight_kg"], 10.0)
        # Сила 40 -> W_cap = 20.0 кг
        self.assertEqual(phys["weight_capacity_kg"], 20.0)
        self.assertEqual(phys["encumbrance_pct"], 0.0)
        # Базовая скорость: 14 + 0.25 * 60 = 29.0
        self.assertEqual(phys["speed_run_mph_est"], 29.0)

        # Перегруз: вес 30 кг при емкости 20 кг -> (30-20)/20 = 50%
        char["carried_weight_kg"] = 30.0
        phys_over = calculate_character_physics(char)
        self.assertEqual(phys_over["encumbrance_pct"], 50.0)
        self.assertLess(phys_over["speed_run_mph_est"], 29.0)

        # Протез ноги с множителем скорости
        char["carried_weight_kg"] = 10.0
        char["prosthetics"] = [{"limb": "left_leg", "athletics_mult": 1.25}]
        phys_leg = calculate_character_physics(char)
        # 29.0 * 1.25 = 36.2
        self.assertGreater(phys_leg["speed_run_mph_est"], 29.0)

    def test_physics_zero_strength_boundary(self) -> None:
        """Граничный случай: нулевая сила не вызывает деления на ноль."""
        char = {
            "id": "ps:zero",
            "name": "Слабый",
            "attributes_base": {"Strength": 0.0},
            "skills_base": {"Athletics": 0.0},
            "carried_weight_kg": 5.0,
        }
        phys = calculate_character_physics(char)
        self.assertEqual(phys["weight_capacity_kg"], 1.0)
        self.assertGreater(phys["encumbrance_pct"], 0.0)
        self.assertGreater(phys["speed_run_mph_est"], 0.0)

    def test_combat_weapon_strength_requirements(self) -> None:
        """Требования оружия к Силе: max(2*Weight, 40*Blunt)."""
        char = {
            "id": "ps:warrior",
            "name": "Хот Лон",
            "attributes_base": {"Strength": 30.0},
            "weapons": [
                {
                    "slot": "weapon_back",
                    "name": "Сокрушитель",
                    "weight_kg": 12.0,
                    "damage_blunt": 1.2,
                }
            ],
            "armour": {
                "chest": {
                    "name": "Панцирь самурая",
                    "stealth_mult": 0.2,
                    "combat_speed_mult": 0.9,
                    "cut_resist": 50.0,
                    "blunt_resist": 40.0,
                    "coverage": 100,
                }
            },
        }
        combat = calculate_character_combat(char)
        self.assertEqual(combat["status"], "unverified_engine_formula")
        w_reqs = combat["weapon_requirements"]
        self.assertEqual(len(w_reqs), 1)
        w0 = w_reqs[0]
        self.assertEqual(w0["name"], "Сокрушитель")
        # req_weight = 2 * 12 = 24.0, req_blunt = 40 * 1.2 = 48.0 -> req = 48.0
        self.assertEqual(w0["strength_required"], 48.0)
        # Сила 30.0 -> дефицит 18.0
        self.assertEqual(w0["strength_deficit"], 18.0)
        self.assertGreater(w0["speed_penalty_pct"], 0.0)

        # Штрафы брони
        penalties = combat["armour_penalties"]
        self.assertEqual(penalties["stealth_mult"], 0.2)
        self.assertEqual(penalties["combat_speed_mult"], 0.9)

        # Покрытие груди
        coverage = combat["body_coverage"]
        self.assertEqual(coverage["chest"]["coverage_pct"], 100)
        self.assertEqual(coverage["chest"]["cut_resist_pct"], 50.0)
        self.assertEqual(coverage["chest"]["blunt_resist_pct"], 40.0)

    def test_combat_unarmed_animal_safe(self) -> None:
        """Безопасная обработка зверей и безоружных персонажей без экипировки."""
        animal = {
            "id": "ps:dog",
            "name": "Костепёс",
            "attributes_base": {"Strength": 25.0},
            "weapons": [],
            "armour": {},
        }
        combat = calculate_character_combat(animal)
        self.assertEqual(combat["weapon_requirements"], [])
        self.assertEqual(combat["armour_penalties"]["combat_speed_mult"], 1.0)
        for zone_cov in combat["body_coverage"].values():
            self.assertEqual(zone_cov["coverage_pct"], 0)

    def test_rendering_squad_includes_calculated_metrics(self) -> None:
        """Рендеринг squad.md выводит расчетные физические и боевые показатели."""
        char = {
            "id": "ps:1",
            "name": "Хот Лон",
            "race": "Скелет",
            "squad_id": "sq_1",
            "status": {"dead": False, "unconscious": False},
            "physics_calculated": {
                "carried_weight_kg": 15.0,
                "weight_capacity_kg": 25.0,
                "encumbrance_pct": 0.0,
                "speed_run_mph_est": 24.0,
            },
            "combat_calculated": {
                "weapon_requirements": [
                    {
                        "name": "Тесак",
                        "strength_required": 20.0,
                        "strength_deficit": 0.0,
                        "speed_penalty_pct": 0.0,
                    }
                ],
                "armour_penalties": {
                    "stealth_mult": 0.8,
                    "athletics_mult": 1.0,
                    "combat_speed_mult": 0.95,
                    "dodge_mult": 1.0,
                },
            },
        }
        squad_data = {"characters": [char], "squads": [{"squad_id": "sq_1", "squad_name": "Штурм", "members": ["Хот Лон"]}]}
        rendered = render_squad(squad_data, "snap-test")
        self.assertIn("Физические показатели (расчёт, unverified)", rendered)
        self.assertIn("вес 15.0 кг", rendered)
        self.assertIn("грузоподъёмность 25.0 кг", rendered)
        self.assertIn("скорость 24.0 миль/ч", rendered)
        self.assertIn("Требования оружия (расчёт, unverified)", rendered)
        self.assertIn("Тесак: сила 20.0 (ОК)", rendered)
        self.assertIn("Штрафы брони (расчёт)", rendered)


if __name__ == "__main__":
    unittest.main()
