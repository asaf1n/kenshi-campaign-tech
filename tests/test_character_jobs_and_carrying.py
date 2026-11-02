"""Тесты декодирования очереди работ (jobs), переноски (carrying), наград и боевых режимов (Этап 2.2)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tools.save_reader import (
    clean_handle,
    clean_job_handle,
    decode_jobs,
    parse_bounties,
    parse_character,
    read_all_platoons,
)
from tools.task_types import TASK_TYPE_NAMES, get_task_name


class TestCharacterJobsAndCarryingSynthetic(unittest.TestCase):
    """Синтетические тесты декодирования работ, переноски и боевых режимов."""

    def test_task_types_dictionary_integrity(self) -> None:
        self.assertEqual(len(TASK_TYPE_NAMES), 289)
        self.assertEqual(get_task_name(58), "JOB_MEDIC")
        self.assertEqual(get_task_name(57), "JOB_REPAIR_ROBOT")
        self.assertEqual(get_task_name(87), "OPERATE_MACHINERY")
        self.assertEqual(get_task_name(124), "OPERATE_STORAGE")
        self.assertEqual(get_task_name(125), "JOB_BUILDER")
        self.assertIsNone(get_task_name(None))
        self.assertEqual(get_task_name(9999), "TASK_9999")

    def test_clean_job_handle(self) -> None:
        # None или пустой
        self.assertIsNone(clean_handle(None))
        self.assertIsNone(clean_job_handle(None))
        # TYPE == 11 (безадресные глобальные задачи типа медика)
        self.assertIsNone(clean_handle((11, 0, 58, 0, 200)))
        self.assertIsNone(clean_job_handle((11, 0, 58, 0, 200)))
        # Все координаты (C, S, I, CS) нулевые
        self.assertIsNone(clean_handle((1, 0, 0, 0, 0)))
        self.assertIsNone(clean_job_handle((1, 0, 0, 0, 0)))
        # Валидный handle станка / сундука / персонажа
        self.assertEqual(clean_handle((1, 5, 500, 1, 600)), (1, 5, 500, 1, 600))
        self.assertEqual(clean_job_handle((1, 5, 500, 1, 600)), (1, 5, 500, 1, 600))

    def test_decode_jobs_synthetic(self) -> None:
        jobs_rec = {
            "type": 67,
            "bool": {"jobs": True},
            "int": {
                "pjob0": 58,
                "pjobT0TYPE": 11, "pjobT0C": 0, "pjobT0S": 100, "pjobT0I": 0, "pjobT0CS": 200,
                "pjob1": 57,
                "pjobT1TYPE": 11, "pjobT1C": 0, "pjobT1S": 101, "pjobT1I": 0, "pjobT1CS": 201,
                "tjob0": 30,
                "tjobT0TYPE": 1, "tjobT0C": 5, "tjobT0S": 500, "tjobT0I": 1, "tjobT0CS": 600,
            },
            "string": {},
        }

        decoded = decode_jobs(jobs_rec)
        self.assertTrue(decoded["enabled"])
        self.assertEqual(len(decoded["primary"]), 2)
        self.assertEqual(decoded["primary"][0]["index"], 0)
        self.assertEqual(decoded["primary"][0]["task_code"], 58)
        self.assertEqual(decoded["primary"][0]["task_name"], "JOB_MEDIC")
        self.assertIsNone(decoded["primary"][0]["target_handle"])

        self.assertEqual(decoded["primary"][1]["index"], 1)
        self.assertEqual(decoded["primary"][1]["task_code"], 57)
        self.assertEqual(decoded["primary"][1]["task_name"], "JOB_REPAIR_ROBOT")
        self.assertIsNone(decoded["primary"][1]["target_handle"])

        self.assertEqual(len(decoded["temporary"]), 1)
        self.assertEqual(decoded["temporary"][0]["task_code"], 30)
        self.assertEqual(decoded["temporary"][0]["target_handle"], (1, 5, 500, 1, 600))

    def test_parse_bounties_multi_faction(self) -> None:
        st_str = {
            "bountyfac0": "defaultEmpireFactionSID",
            "bountyfac1": "11624-Dialogue (10).mod",
        }
        st_int = {
            "bountyexp0": 2000,
            "crimes0": 1,
            "bountyexp1": 1500,
            "crimes1": 0,
        }
        bounties = parse_bounties(st_str, st_int)
        self.assertTrue(bounties["has_bounty"])
        self.assertEqual(bounties["total_amount"], 3500)
        self.assertEqual(len(bounties["entries"]), 2)
        self.assertEqual(bounties["entries"][0]["faction_sid"], "defaultEmpireFactionSID")
        self.assertEqual(bounties["entries"][0]["amount"], 2000)
        self.assertEqual(bounties["entries"][1]["faction_sid"], "11624-Dialogue (10).mod")
        self.assertEqual(bounties["entries"][1]["amount"], 1500)

    def test_parse_character_carrying_and_modes_synthetic(self) -> None:
        # Персонаж 1: несёт на руках
        inst_carrier = {"id": "char_01", "target": "human_template", "states": ["st_36_1"]}
        # Персонаж 2: кого несут
        inst_victim = {"id": "char_02", "target": "human_template", "states": ["st_36_2"]}

        by_sid = {
            "st_36_1": {
                "type": 36,
                "string": {
                    "name": "Несущий Персонаж",
                    "bountyfac0": "1083-gamedata.base",
                },
                "int": {
                    "portrait_serial": 777,
                    "bountyexp0": 15000,
                    "crimes0": 2,
                    "carryingTYPE": 1, "carryingC": 54, "carryingS": 99999, "carryingI": 6, "carryingCS": 88888,
                },
                "bool": {
                    "carrying": True,
                    "taunt mode": True,
                    "ranged mode": True,
                    "passive mode": False,
                    "defensive mode": False,
                },
            },
            "st_36_2": {
                "type": 36,
                "string": {
                    "name": "Переносимый Персонаж",
                },
                "int": {
                    "portrait_serial": 888,
                    "carryingTYPE": 1, "carryingC": 2, "carryingS": 11111, "carryingI": 4, "carryingCS": 22222,
                },
                "bool": {
                    "carrying": False,
                    "taunt mode": False,
                    "ranged mode": False,
                },
            },
        }

        squad_meta = {"squad_id": "squad_test", "squad_name": "Test Squad"}

        carrier = parse_character(inst_carrier, by_sid, squad_meta)
        self.assertIsNotNone(carrier)
        self.assertTrue(carrier["carrying"]["is_carrying"])
        self.assertEqual(carrier["carrying"]["target_handle"], (1, 54, 99999, 6, 88888))
        self.assertIsNone(carrier["carrying"]["carried_by"])
        self.assertTrue(carrier["bounties"]["has_bounty"])
        self.assertEqual(carrier["bounties"]["total_amount"], 15000)
        # Боевые режимы (hold отсутствует в игре, отсутствующие режимы = None)
        modes = carrier["combat_modes"]
        self.assertNotIn("hold", modes)
        self.assertTrue(modes["taunt"])
        self.assertTrue(modes["ranged"])
        self.assertFalse(modes["passive"])
        self.assertFalse(modes["defensive"])
        self.assertIsNone(modes["chase"])
        self.assertIsNone(modes["stealth"])
        self.assertIsNone(modes["non_combat"])

        victim = parse_character(inst_victim, by_sid, squad_meta)
        self.assertIsNotNone(victim)
        self.assertFalse(victim["carrying"]["is_carrying"])
        self.assertIsNone(victim["carrying"]["target_handle"])
        self.assertEqual(victim["carrying"]["carried_by"], (1, 2, 11111, 4, 22222))
        self.assertFalse(victim["bounties"]["has_bounty"])


class TestCharacterJobsAndCarryingRealSave(unittest.TestCase):
    """Интеграционные тесты на реальных сохранениях Kenshi."""

    def test_real_save_autosave0_jobs_and_modes(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        save_dir = Path(local_appdata) / "kenshi" / "save" / "autosave0"
        if not (save_dir / "quick.save").is_file():
            self.skipTest(f"Сейв autosave0 не найден: {save_dir}")

        _, chars, _ = read_all_platoons(save_dir)
        self.assertGreaterEqual(len(chars), 20)

        # 1. Проверка медиков: Доктор Чон и Хоббс (clean_job_handle очищает handle до None)
        hobbs = next((c for c in chars if c["name"] in ("Хоббс", "Hobbs")), None)
        self.assertIsNotNone(hobbs)
        hobbs_primary = [(pj["task_name"], pj["target_handle"]) for pj in hobbs["jobs"]["primary"]]
        self.assertIn(("JOB_MEDIC", None), hobbs_primary)
        self.assertIn(("JOB_REPAIR_ROBOT", None), hobbs_primary)

        medic = next((c for c in chars if c["name"] in ("Доктор Чон", "Doctor Chung")), None)
        self.assertIsNotNone(medic)
        primary_tasks = [pj["task_name"] for pj in medic["jobs"]["primary"]]
        self.assertIn("JOB_MEDIC", primary_tasks)
        self.assertIn("JOB_REPAIR_ROBOT", primary_tasks)

        # 2. Проверка фермера/производственника: Мью
        farmer = next((c for c in chars if c["name"] in ("Мью", "Miu")), None)
        self.assertIsNotNone(farmer)
        farmer_tasks = [pj["task_name"] for pj in farmer["jobs"]["primary"]]
        self.assertIn("OPERATE_STORAGE", farmer_tasks)
        self.assertIn("OPERATE_MACHINERY", farmer_tasks)

        # 3. Проверка строителя: Зелёный палец
        builder = next((c for c in chars if c["name"] in ("Зелёный палец", "Green Finger")), None)
        self.assertIsNotNone(builder)
        builder_tasks = [pj["task_name"] for pj in builder["jobs"]["primary"]]
        self.assertIn("JOB_BUILDER", builder_tasks)

        # 4. Проверка боевого режима ranged у Зелёного (стрелок, hold отсутствует)
        green = next((c for c in chars if c["name"] in ("Зелёный", "Green")), None)
        self.assertIsNotNone(green)
        self.assertNotIn("hold", green["combat_modes"])
        self.assertTrue(green["combat_modes"]["ranged"])
        self.assertIsInstance(green["combat_modes"]["taunt"], bool)

    def test_real_save_quicksave_hobbs_carrying(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        save_dir = Path(local_appdata) / "kenshi" / "save" / "quicksave"
        if not (save_dir / "quick.save").is_file():
            self.skipTest(f"Сейв quicksave не найден: {save_dir}")

        _, chars, _ = read_all_platoons(save_dir)
        hobbs = next((c for c in chars if c["name"] in ("Хоббс", "Hobbs")), None)
        self.assertIsNotNone(hobbs)

        # Хоббс в quicksave несёт персонажа на руках
        carry = hobbs["carrying"]
        self.assertTrue(carry["is_carrying"])
        self.assertIsNotNone(carry["target_handle"])
        self.assertIsNone(carry["carried_by"])
        self.assertEqual(carry["target_handle"][0], 1)

        # Награда Хоббса
        self.assertTrue(hobbs["bounties"]["has_bounty"])
        self.assertEqual(hobbs["bounties"]["total_amount"], 457)
        self.assertEqual(hobbs["bounties"]["entries"][0]["faction_sid"], "11624-Dialogue (10).mod")

        # Персонаж, которого несут («Вынос мозга»)
        brain = next((c for c in chars if "мозг" in c["name"].lower()), None)
        self.assertIsNotNone(brain)
        self.assertFalse(brain["carrying"]["is_carrying"])
        self.assertIsNone(brain["carrying"]["target_handle"])
        self.assertIsNotNone(brain["carrying"]["carried_by"])
        self.assertEqual(brain["carrying"]["carried_by"][0], 1)

        # Персонаж с несколькими наградами (Пиа)
        pia = next((c for c in chars if c["name"] in ("Пиа", "Pia")), None)
        self.assertIsNotNone(pia)
        self.assertTrue(pia["bounties"]["has_bounty"])
        self.assertEqual(len(pia["bounties"]["entries"]), 2)
        self.assertEqual(pia["bounties"]["total_amount"], 3713)


if __name__ == "__main__":
    unittest.main()
