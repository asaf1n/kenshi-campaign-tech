"""Тесты модуля prosthetics: расчет ОЗ протезов, разбор состояний конечностей и шаблонов."""
import unittest

from tools import prosthetics


class ProstheticsTests(unittest.TestCase):
    def test_hp_max_calculation_and_rounding(self) -> None:
        """ОЗ Макс протеза рассчитывается с округлением до 5 по контрольным точкам интерфейса."""
        # Хот Лон: level 95 -> 245
        t_arm = {"HP": 50, "HP 1": 255}
        self.assertEqual(prosthetics.hp_max(t_arm, 95), 245)
        self.assertEqual(prosthetics.hp_max(t_arm, 80), 215)

        # Нога разведчика: level 95 -> 145
        t_scout = {"HP": 50, "HP 1": 150}
        self.assertEqual(prosthetics.hp_max(t_scout, 95), 145)

        # Эконом-рука / малый протез: level 95 -> 85
        t_econ = {"HP": 30, "HP 1": 88}
        self.assertEqual(prosthetics.hp_max(t_econ, 95), 85)

        # Неполные данные дают None
        self.assertIsNone(prosthetics.hp_max(t_arm, None))
        self.assertIsNone(prosthetics.hp_max({}, 95))

    def test_limb_states_and_read_limbs(self) -> None:
        """Разбор 2-битных состояний конечностей (0=своя, 1=потеряна, 2=протез) и связка с предметами."""
        # left_arm (индекс 0) = 2 (протез), right_arm (индекс 1) = 1 (потеряна) -> (2 << 0) | (1 << 2) = 2 | 4 = 6
        states = prosthetics.limb_states(6)
        self.assertEqual(states, {"left_arm": 2, "right_arm": 1})

        # read_limbs: корректная связка предмета протеза и потерянной конечности
        med = {
            "int": {"limbs": 6},
            "refs": {"limbs": [{"sid": "p-arm-1", "v": [0, 0, 0]}]},
        }
        by = {"p-arm-1": {"type": 42, "id": 101, "name": "Эконом-рука"}}
        warnings: list[str] = []
        items, lost = prosthetics.read_limbs(med, by, warnings, who="Хот Лон")
        self.assertEqual(list(items.keys()), ["left_arm"])
        self.assertEqual(items["left_arm"]["name"], "Эконом-рука")
        self.assertEqual(lost, ["right_arm"])
        self.assertEqual(warnings, [])

    def test_template_view_and_grades(self) -> None:
        """Извлечение диапазонов шаблона и грейдов по уровню."""
        self.assertEqual(prosthetics.CONFIRMED_GRADES.get(95), "Мастерский")
        self.assertEqual(prosthetics.CONFIRMED_GRADES.get(80), "Специальный")
        self.assertIsNone(prosthetics.CONFIRMED_GRADES.get(60))

        t = {
            "HP": 50, "HP 1": 150,
            "athletics mult": 1.0, "athletics mult 1": 1.4,
            "stealth mult": 1.0, "stealth mult 1": 1.0,  # нейтральное опускается
            "weight kg": 10.0,
            "combat speed mult": 1.1,
        }
        view = prosthetics.template_view(t)
        self.assertIn("hp", view["template_range"])
        self.assertEqual(view["template_range"]["hp"], [50.0, 150.0])
        self.assertEqual(view["template_range"]["athletics_mult"], [1.0, 1.4])
        self.assertNotIn("stealth_mult", view.get("template_range", {}))
        self.assertEqual(view["template"]["weight_kg"], 10.0)
        self.assertEqual(view["template"]["combat_speed_mult"], 1.1)
