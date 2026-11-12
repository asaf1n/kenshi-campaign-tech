"""Тесты модуля itemstats: confirmed combos, формулы урона, веса, брони и протезов."""
import unittest

from tools import itemstats


class ItemStatsTests(unittest.TestCase):
    def test_confirmed_prosthetic_limbs(self) -> None:
        """CONFIRMED_PROSTHETIC_LIMBS содержит все 4 конечности (обе ноги и обе руки)."""
        self.assertEqual(
            itemstats.CONFIRMED_PROSTHETIC_LIMBS,
            ("left_leg", "right_leg", "left_arm", "right_arm"),
        )
        for limb in ("left_leg", "right_leg", "left_arm", "right_arm"):
            self.assertIn(limb, itemstats.CONFIRMED_PROSTHETIC_LIMBS)
        for invalid in ("head", "torso", "stomach", "chest"):
            self.assertNotIn(invalid, itemstats.CONFIRMED_PROSTHETIC_LIMBS)

    def test_unverified_class_combos_preserved(self) -> None:
        """Неподтверждённые карточками сочетания оружия и брони строго исключены из confirmed."""
        # Тяжёлое оружие (3) со Скелетами-кузнецами (Skeleton Smiths) расходится по весу
        self.assertNotIn((3, "Skeleton Smiths"), itemstats.CONFIRMED_MELEE_COMBOS)
        # Рубачи (4) с Древними (Ancient) расходятся по весу
        self.assertNotIn((4, "Ancient"), itemstats.CONFIRMED_MELEE_COMBOS)
        # Катаны (0) с Катунским Кулибиным (Catun Scrapmaster) расходятся по весу
        self.assertNotIn((0, "Catun Scrapmaster"), itemstats.CONFIRMED_MELEE_COMBOS)
        # Ткань (0, 0) не имеет карточек
        self.assertNotIn((0, 0), itemstats.CONFIRMED_ARMOUR_COMBOS)

    def test_grade_for(self) -> None:
        """Определение грейда брони и арбалета по уровню."""
        self.assertEqual(itemstats.grade_for("ARMOUR", 95), "Мастерский")
        self.assertEqual(itemstats.grade_for("ARMOUR", 80), "Специальный")
        self.assertEqual(itemstats.grade_for("ARMOUR", 40), "Стандартный")
        self.assertIsNone(itemstats.grade_for("ARMOUR", 50))
        self.assertIsNone(itemstats.grade_for("ARMOUR", None))
        self.assertEqual(itemstats.grade_for("CROSSBOW", 95), "Мастерский")
        self.assertIsNone(itemstats.grade_for("CROSSBOW", 80))

    def test_melee_damage_and_weight(self) -> None:
        """Расчёт урона и веса холодного оружия."""
        t = {"cut damage multiplier": 1.0, "blunt damage multiplier": 0.5, "weight kg": 2.0, "weight mult": 1.0}
        manuf = {"cut damage mod": 1.1, "blunt damage mod": 1.0}
        cut, blunt = itemstats.melee_damage(t, manuf, level=70)
        # cut = 1.0 * 1.1 * (0.3 + 0.017 * 70) = 1.1 * 1.49 = 1.639
        self.assertAlmostEqual(cut, 1.639, places=3)
        # blunt = 0.5 * 1.0 * 70 / 50 = 0.7
        self.assertAlmostEqual(blunt, 0.7, places=3)
        # weight = max(2.0, 0.7 * 40 * 0.5 * 1.0) = max(2.0, 14.0) = 14.0
        w = itemstats.melee_weight(t, blunt, inv_weight_mult=0.5)
        self.assertAlmostEqual(w, 14.0, places=3)

    def test_armour_resistances_and_weight(self) -> None:
        """Расчёт сопротивлений брони и веса."""
        t = {"class": 2, "material type": 2, "slot": 8, "cut def bonus": 0.0, "blunt def bonus": 0.0,
             "pierce def mult": 1.0, "refs:part coverage": {"chest": [100, 0, 0]}}
        res = itemstats.armour_resistances(t, e=90.0)
        self.assertIsNotNone(res)
        self.assertIn("cut_pct", res)
        self.assertIn("blunt_pct", res)
        self.assertIn("harpoon", res)
        w = itemstats.armour_weight(t, is_chest=lambda sid: sid == "chest")
        self.assertIsNotNone(w)
        self.assertGreater(w, 0.0)
