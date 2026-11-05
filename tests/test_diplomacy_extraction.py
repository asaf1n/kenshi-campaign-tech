"""Тесты извлечения дипломатии и геополитического состояния мира Kenshi (Этап 2.1)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tools.save_reader import (
    extract_diplomacy,
    extract_world_state,
    read_quick_save,
)


class TestDiplomacySynthetic(unittest.TestCase):
    """Синтетические модульные тесты разбора дипломатии."""

    def test_extract_diplomacy_synthetic(self) -> None:
        records = [
            # Запись игрока (sid 1000)
            {
                "sid": "player_rec_sid",
                "type": 37,
                "name": "Безымянные",
                "string": {"gamedata stringID": "204-gamedata.base"},
                "float": {},
            },
            # Фракция Шеков (союзник)
            {
                "sid": "shek_rec_sid",
                "type": 37,
                "name": "Королевство шеков",
                "string": {
                    "gamedata stringID": "11624-Dialogue (10).mod",
                    "relationSID0": "player_rec_sid",
                },
                "float": {
                    "relation0": 100.0,
                    "trust0": 0.0,
                    "trustNeg0": 0.0,
                },
            },
            # Святая Нация (враг)
            {
                "sid": "hn_rec_sid",
                "type": 37,
                "name": "Святая Нация",
                "string": {
                    "gamedata stringID": "1083-gamedata.base",
                    "relationSID0": "player_rec_sid",
                },
                "float": {
                    "relation0": -100.0,
                    "trust0": 0.0,
                },
            },
            # Нейтральная фракция без связи (не подменяется на 0.0!)
            {
                "sid": "traders_rec_sid",
                "type": 37,
                "name": "Вольные купцы",
                "string": {
                    "gamedata stringID": "1229-gamedata.base",
                },
                "float": {},
            },
        ]

        dip = extract_diplomacy(records, player_faction_sid="204-gamedata.base")
        self.assertEqual(dip["player_faction_sid"], "204-gamedata.base")
        self.assertEqual(dip["player_faction_name"], "Безымянные")
        self.assertEqual(len(dip["relations"]), 3)

        # Сортировка: сначала известные отношения по убыванию (+100.0, затем -100.0), затем unknown (None)
        self.assertEqual(dip["relations"][0]["faction_sid"], "11624-Dialogue (10).mod")
        self.assertEqual(dip["relations"][0]["relation"], 100.0)

        self.assertEqual(dip["relations"][1]["faction_sid"], "1083-gamedata.base")
        self.assertEqual(dip["relations"][1]["relation"], -100.0)

        # Не связанная фракция: relation is None (не подменяется на 0.0)
        self.assertEqual(dip["relations"][2]["faction_sid"], "1229-gamedata.base")
        self.assertIsNone(dip["relations"][2]["relation"])

        summary = dip["summary"]
        self.assertEqual(summary["total_factions"], 3)

    def test_extract_world_state_synthetic(self) -> None:
        records = [
            # Запись 56 с уникальными NPC (player в bool)
            {
                "sid": "world_state_56",
                "type": 56,
                "string": {
                    "usedUniques0": "hobbs_sid",
                    "usedUniques1": "phoenix_sid",
                },
                "bool": {
                    "usedUniquesPlayer0": False,
                    "usedUniquesPlayer1": True,
                },
                "int": {
                    "usedUniquesState0": 1,
                    "usedUniquesState1": 2,
                    "usedUniques1handleTYPE": 1,
                    "usedUniques1handleC": 200,
                    "usedUniques1handleS": 300,
                    "usedUniques1handleI": 1,
                    "usedUniques1handleCS": 400,
                },
            },
            # Город обычный (тип 94)
            {
                "sid": "town_hub",
                "type": 94,
                "name": "Town state Хаб",
                "string": {"faction": "Техохотники"},
                "int": {},
            },
            # Город с переопределением (тип 94)
            {
                "sid": "town_blister",
                "type": 94,
                "name": "Town state Бугристый Холм",
                "string": {
                    "faction": "Святая Нация",
                    "replacementTown": "rep_blister_destroyed_sid",
                },
                "int": {},
            },
        ]

        gdb_mock = {
            "phoenix_sid": ["Holy Lord Phoenix", "Святой Лорд Феникс"],
            "hobbs_sid": ["Hobbs", "Хоббс"],
            "rep_blister_destroyed_sid": ["Blister Hill (override) destroyed", "Бугристый Холм (разрушен)"],
        }

        ws = extract_world_state(records, gamedb_inst=gdb_mock)
        uniques = ws["unique_npcs"]
        self.assertEqual(len(uniques), 2)
        self.assertEqual(uniques[0]["name_ru"], "Хоббс")
        self.assertEqual(uniques[0]["state_code"], 1)
        self.assertNotIn("state", uniques[0])
        self.assertIs(uniques[0]["player"], False)

        self.assertEqual(uniques[1]["name_ru"], "Святой Лорд Феникс")
        self.assertEqual(uniques[1]["state_code"], 2)
        self.assertIs(uniques[1]["player"], True)
        self.assertIsNotNone(uniques[1]["handle"])

        overrides = ws["town_overrides"]
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0]["name"], "Бугристый Холм")
        self.assertEqual(overrides[0]["replacement_name"], "Blister Hill (override) destroyed")
        self.assertEqual(overrides[0]["replacement_name_ru"], "Бугристый Холм (разрушен)")

        # Сводка по числовым кодам состояния
        self.assertEqual(ws["summary"]["total_uniques"], 2)
        self.assertEqual(ws["summary"]["state_0_count"], 0)
        self.assertEqual(ws["summary"]["state_1_count"], 1)
        self.assertEqual(ws["summary"]["state_2_count"], 1)
        self.assertEqual(ws["summary"]["overridden_towns_count"], 1)


@unittest.skip("LIVE-SLOT: состояние живого слота сохранения меняется во время игры; сквозная проверка выполняется в check_live.py и check_e2e_real.py")
class TestDiplomacyRealSave(unittest.TestCase):
    """Интеграционные тесты на реальном сохранении Kenshi."""

    def setUp(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        self.save_dir = Path(local_appdata) / "kenshi" / "save" / "autosave0"
        if not (self.save_dir / "quick.save").is_file():
            self.skipTest(f"Реальный сейв autosave0 не найден: {self.save_dir}")

    def test_real_save_diplomacy_and_world_state(self) -> None:
        from tools.gamedb import load_index
        os.makedirs("tests/.cache", exist_ok=True)
        idx, _ = load_index("tests/.cache")

        qs = read_quick_save(self.save_dir, gamedb_inst=idx)

        # 1. Проверка дипломатии
        dip = qs["diplomacy"]
        self.assertEqual(dip["player_faction_sid"], "204-gamedata.base")
        self.assertEqual(dip["summary"]["total_factions"], 103)

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
        self.assertEqual(ws["summary"]["total_uniques"], 79)
        self.assertEqual(ws["summary"]["state_0_count"], 10)
        self.assertEqual(ws["summary"]["state_1_count"], 62)
        self.assertEqual(ws["summary"]["state_2_count"], 7)
        self.assertEqual(ws["summary"]["total_towns"], 554)
        self.assertEqual(ws["summary"]["overridden_towns_count"], 9)

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
