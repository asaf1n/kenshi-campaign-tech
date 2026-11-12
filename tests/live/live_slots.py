"""Интеграционные тесты живых слотов сохранений Kenshi (LIVE-SLOT).

Вынесены из регулярного discover-набора юнит-тестов (имя файла без префикса test_),
поскольку состояние живых слотов сохранения эволюционирует во время игры.
Тесты проверяют корректность извлечения инфраструктуры, инвентарей, профессий,
переноски, наград, дипломатии и мирового состояния на реальных слотах autosave0 и quicksave.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest

from tools.gamedb import load_index
from tools.save_reader import (
    DEFAULT_PLAYER_FACTION,
    read_all_platoons,
    read_player_property,
    read_quick_save,
)


class TestBaseProductionRealSave(unittest.TestCase):
    """Интеграционные тесты извлечения производства и инвентарей на реальном сохранении autosave0."""

    def setUp(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        self.save_dir = Path(local_appdata) / "kenshi" / "save" / "autosave0"
        if not (self.save_dir / "quick.save").is_file():
            self.skipTest(f"Сейв autosave0 не найден: {self.save_dir}")

    def test_real_save_storage_inventories(self) -> None:
        prop = read_player_property(self.save_dir, DEFAULT_PLAYER_FACTION)
        all_buildings = []
        for b in prop["buildings"]:
            all_buildings.append(b)
            all_buildings.extend(b.get("interior", []))

        all_items = [it for b in all_buildings for it in b.get("storage_items", [])]
        self.assertGreaterEqual(len(all_items), 50)

        # Наличие предметов с handle владельца
        items_with_owner = [it for it in all_items if it.get("owned_by") is not None]
        self.assertGreaterEqual(len(items_with_owner), 10)

        # 1. Запасы пеньки (sid: 1965-gamedata.base)
        hemp_items = [it for it in all_items if it.get("base_data_sid") == "1965-gamedata.base"]
        total_hemp = sum(it.get("quantity") or 1 for it in hemp_items)
        self.assertGreaterEqual(total_hemp, 100)

        # 2. Запасы медикаментов (sid: 1359-gamedata.base - Advanced First Aid Kit, 515-gamedata.base - Standard)
        med_items = [it for it in all_items if it.get("base_data_sid") in ("1359-gamedata.base", "515-gamedata.base")]
        total_meds = sum(it.get("quantity") or 1 for it in med_items)
        self.assertGreaterEqual(total_meds, 10)

        # 3. Древние научные книги (sid: 43953-rebirth.mod)
        ancient_books = [it for it in all_items if it.get("base_data_sid") == "43953-rebirth.mod"]
        total_books = sum(it.get("quantity") or 1 for it in ancient_books)
        self.assertGreaterEqual(total_books, 1)


class TestCharacterJobsAndCarryingRealSave(unittest.TestCase):
    """Интеграционные тесты профессий, боевых режимов и наград на реальных сохранениях."""

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

        # 3. Проверка производственника/строителя: Зелёный палец
        builder = next((c for c in chars if c["name"] in ("Зелёный палец", "Green Finger")), None)
        self.assertIsNotNone(builder)
        builder_tasks = [pj["task_name"] for pj in builder["jobs"]["primary"]]
        self.assertTrue(any(t in ("JOB_BUILDER", "OPERATE_STORAGE", "OPERATE_MACHINERY") for t in builder_tasks))

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

        # Структура переноски у Хоббса
        carry = hobbs["carrying"]
        self.assertIsInstance(carry["is_carrying"], bool)
        if carry["is_carrying"]:
            self.assertIsNotNone(carry["target_handle"])
            self.assertIsNone(carry["carried_by"])
            self.assertEqual(carry["target_handle"][0], 1)
        else:
            self.assertIsNone(carry["target_handle"])

        # Награда Хоббса
        self.assertTrue(hobbs["bounties"]["has_bounty"])
        self.assertGreater(hobbs["bounties"]["total_amount"], 0)
        self.assertEqual(hobbs["bounties"]["entries"][0]["faction_sid"], "11624-Dialogue (10).mod")

        # Персонаж «Вынос мозга»
        brain = next((c for c in chars if "мозг" in c["name"].lower()), None)
        self.assertIsNotNone(brain)
        self.assertIsInstance(brain["carrying"]["is_carrying"], bool)
        if brain["carrying"]["carried_by"] is not None:
            self.assertEqual(brain["carrying"]["carried_by"][0], 1)

        # Персонаж с несколькими наградами (Пиа)
        pia = next((c for c in chars if c["name"] in ("Пиа", "Pia")), None)
        self.assertIsNotNone(pia)
        self.assertTrue(pia["bounties"]["has_bounty"])
        self.assertGreaterEqual(len(pia["bounties"]["entries"]), 1)
        self.assertGreater(pia["bounties"]["total_amount"], 0)


class TestDiplomacyRealSave(unittest.TestCase):
    """Интеграционные тесты дипломатии на реальном сохранении Kenshi."""

    def setUp(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        self.save_dir = Path(local_appdata) / "kenshi" / "save" / "autosave0"
        if not (self.save_dir / "quick.save").is_file():
            self.skipTest(f"Реальный сейв autosave0 не найден: {self.save_dir}")

    def test_real_save_diplomacy_and_world_state(self) -> None:
        os.makedirs("tests/.cache", exist_ok=True)
        idx, _ = load_index("tests/.cache")

        qs = read_quick_save(self.save_dir, gamedb_inst=idx)

        # 1. Проверка дипломатии
        dip = qs["diplomacy"]
        self.assertEqual(dip["player_faction_sid"], "204-gamedata.base")
        self.assertGreaterEqual(dip["summary"]["total_factions"], 100)

        # Шеки и Шиноби — союзники (+100.0)
        shek = next((r for r in dip["relations"] if r["faction_sid"] == "11624-Dialogue (10).mod"), None)
        self.assertIsNotNone(shek)
        self.assertEqual(shek["relation"], 100.0)

        shinobi = next((r for r in dip["relations"] if r["faction_sid"] == "14491-rebirth.mod"), None)
        self.assertIsNotNone(shinobi)
        self.assertEqual(shinobi["relation"], 100.0)

        # Святая Нация — война (-100.0)
        holy_nation = next((r for r in dip["relations"] if r["faction_sid"] == "1083-gamedata.base"), None)
        self.assertIsNotNone(holy_nation)
        self.assertEqual(holy_nation["relation"], -100.0)

        # 2. Проверка состояния мира
        ws = qs["world_state"]
        self.assertGreaterEqual(ws["summary"]["total_uniques"], 70)
        self.assertGreaterEqual(ws["summary"]["state_0_count"], 5)
        self.assertGreaterEqual(ws["summary"]["state_1_count"], 50)
        self.assertGreaterEqual(ws["summary"]["state_2_count"], 5)
        self.assertGreaterEqual(ws["summary"]["total_towns"], 500)
        self.assertGreaterEqual(ws["summary"]["overridden_towns_count"], 5)

        # Проверка пленения/уничтожения Феникса
        phoenix = next((u for u in ws["unique_npcs"] if u["sid"] == "17225-Dialogue.mod"), None)
        self.assertIsNotNone(phoenix)
        self.assertEqual(phoenix["state_code"], 2)
        self.assertIs(phoenix["player"], True)
        self.assertIsNotNone(phoenix["handle"])

        # Проверка Короля Пыли
        dust_king = next((u for u in ws["unique_npcs"] if u["sid"] == "2849-gamedata.base"), None)
        self.assertIsNotNone(dust_king)
        self.assertEqual(dust_king["state_code"], 2)
        self.assertIs(dust_king["player"], True)

        # Проверка разрушения Бугристого Холма
        blister_hill = next((t for t in ws["town_overrides"] if "Бугристый Холм" in t["name"] or "Blister" in t["name"]), None)
        self.assertIsNotNone(blister_hill)
        self.assertIn("Blister Hill (override) destroyed", blister_hill["replacement_name"])


if __name__ == "__main__":
    unittest.main()
