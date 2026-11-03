"""Тесты извлечения инфраструктуры и производства баз игрока (Этап 2.3)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tools.converter_worker import extend_buildings
from tools.io_utils import ExtractionError
from tools.save_reader import (
    DEFAULT_PLAYER_FACTION,
    collect_items,
    read_player_property,
)


class TestBaseProductionSynthetic(unittest.TestCase):
    """Синтетические проверки извлечения состояния зданий, энергии, крафта и хранилищ."""

    def test_production_overlay_preserves_storage_and_zone_identity(self) -> None:
        buildings = [{"id": "house", "zone": zone, "name": "Дом", "interior": [
            {"id": "bench", "storage": [{"sid": "iron", "quantity": 3}]}]} for zone in ("1.1", "2.2")]
        native = [{"id": "house", "zone": zone, "name": "Другое имя", "interior": [
            {"id": "bench", "zone": zone, "power_on": enabled, "storage_items": []}]} for zone, enabled in (
                ("1.1", True), ("2.2", False))]
        extend_buildings(buildings, native)
        self.assertEqual([b["interior"][0]["power_on"] for b in buildings], [True, False])
        for building in buildings:
            self.assertEqual(building["name"], "Дом")
            self.assertEqual(building["interior"][0]["storage"], [{"sid": "iron", "quantity": 3}])
        with self.assertRaises(ExtractionError):
            extend_buildings(buildings, native[:1])

    def test_collect_items_synthetic_rich_attributes(self) -> None:
        by_sid = {
            "cont_01": {
                "type": 41,
                "instances": [
                    {"target": "item_weapon"},
                    {"target": "item_medkit"},
                    {"target": "item_hemp"},
                ],
            },
            "item_weapon": {
                "type": 42,
                "sid": "item_weapon",
                "string": {
                    "base data sid": "katana_base",
                    "material sid": "catun_no1",
                    "company sid": "smith_grade",
                    "section": "weapon",
                },
                "float": {
                    "quality": 88.5,
                    "wear": 0.05,
                    "dam": 12.3,
                    "stun": 2.1,
                },
                "int": {
                    "quantity": 1,
                    "inventory x": 0,
                    "inventory y": 0,
                    "item function": 0,
                    "ownedbyTYPE": 1, "ownedbyC": 5, "ownedbyS": 500, "ownedbyI": 1, "ownedbyCS": 600,
                },
                "bool": {"unique": True},
            },
            "item_medkit": {
                "type": 42,
                "sid": "item_medkit",
                "string": {"base data sid": "medkit_adv", "section": "medical"},
                "float": {"charges": 150.0},
                "int": {"quantity": 1},
                "bool": {},
            },
            "item_hemp": {
                "type": 42,
                "sid": "item_hemp",
                "string": {"base data sid": "hemp_resource", "section": "resource"},
                "float": {},
                "int": {"quantity": 50},
                "bool": {},
            },
        }

        items = collect_items("cont_01", by_sid, "storage>cont_01")
        self.assertEqual(len(items), 3)

        # Оружие с повреждениями и handle владельца
        weap = next(it for it in items if it["item_sid"] == "item_weapon")
        self.assertEqual(weap["base_data_sid"], "katana_base")
        self.assertEqual(weap["material_sid"], "catun_no1")
        self.assertEqual(weap["company_sid"], "smith_grade")
        self.assertEqual(weap["quality"], 88.5)
        self.assertEqual(weap["wear"], 0.05)
        self.assertEqual(weap["dam"], 12.3)
        self.assertEqual(weap["stun"], 2.1)
        self.assertTrue(weap["unique"])
        self.assertEqual(weap["owned_by"], [1, 5, 500, 1, 600])
        self.assertIn("bool", weap["raw"])

        # Аптечка с зарядами (wear/dam/stun отсутствуют)
        med = next(it for it in items if it["item_sid"] == "item_medkit")
        self.assertEqual(med["charges"], 150.0)
        self.assertIsNone(med["wear"])
        self.assertIsNone(med["dam"])
        self.assertIsNone(med["stun"])

        # Ресурс со стаком (wear/dam/stun отсутствуют)
        hemp = next(it for it in items if it["item_sid"] == "item_hemp")
        self.assertEqual(hemp["quantity"], 50)
        self.assertIsNone(hemp["wear"])

    def test_building_template_resolution_synthetic(self) -> None:
        import tempfile
        from tests.test_save_reader import _make_file, _make_record

        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            zdir = tdir / "zone"
            zdir.mkdir()

            r_bench = _make_record(
                kind=35, sid="bldg-bench", name="0",
                bools={"is inside building": False, "is complete": True, "power on": True},
                strings={"owner faction ID": DEFAULT_PLAYER_FACTION, "usagenode_0": "usenode001"},
            )
            r30 = _make_record(
                kind=30, sid="0-buildinglist", name="Buildings",
                instances=[{"id": "inst-bench", "target": "tpl_bench", "states": ["bldg-bench"]}],
            )
            (zdir / "zone.10.10.zone").write_bytes(_make_file([r30, r_bench]))

            # Без GameDB: имя остается None (т.к. r.name="0" и b_str["name"] нет)
            prop = read_player_property(tdir, DEFAULT_PLAYER_FACTION)
            self.assertEqual(len(prop["buildings"]), 1)
            b = prop["buildings"][0]
            self.assertIsNone(b["name"])
            self.assertIsNone(b["template_name"])
            self.assertEqual(b["usage_nodes"], ["usenode001"])

            # С GameDB: имя шаблона и имя постройки разрешаются
            gdb = {"tpl_bench": ["Robotics Bench", "Верстак робототехники"]}
            prop_gdb = read_player_property(tdir, DEFAULT_PLAYER_FACTION, gamedb_inst=gdb)
            b_gdb = prop_gdb["buildings"][0]
            self.assertEqual(b_gdb["name"], "Верстак робототехники")
            self.assertEqual(b_gdb["template_name"], "Верстак робототехники")


class TestBaseProductionRealSave(unittest.TestCase):
    """Интеграционные тесты на реальном сохранении autosave0."""

    def setUp(self) -> None:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        self.save_dir = Path(local_appdata) / "kenshi" / "save" / "autosave0"
        if not (self.save_dir / "quick.save").is_file():
            self.skipTest(f"Сейв autosave0 не найден: {self.save_dir}")

    def test_real_save_buildings_and_infrastructure(self) -> None:
        prop = read_player_property(self.save_dir, DEFAULT_PLAYER_FACTION)
        top_buildings = prop["buildings"]
        self.assertGreaterEqual(len(top_buildings), 40)

        all_buildings = []
        for b in top_buildings:
            all_buildings.append(b)
            all_buildings.extend(b.get("interior", []))

        # Всего построек игрока на базе
        self.assertGreaterEqual(len(all_buildings), 150)

        # Проверка чтения реальных имен из string.name (не забиты "0")
        named_buildings = [b for b in all_buildings if b.get("name") and b.get("name") != "0"]
        self.assertGreaterEqual(len(named_buildings), 100)

        # 1. Аккумуляторы (Battery Bank)
        batteries = [b for b in all_buildings if b.get("battery_charge") is not None and b.get("battery_charge") > 0]
        self.assertGreaterEqual(len(batteries), 2)
        for bat in batteries:
            self.assertEqual(bat["battery_charge"], 120.0)
            self.assertTrue(bat["power_on"])
            self.assertTrue(bat["gets_battery"])
            self.assertTrue(bat["complete"])

        # 2. Недостроенная гарпунная турель (шаблон 2870-gamedata.base)
        turret = next((b for b in all_buildings if b.get("template_sid") == "2870-gamedata.base" or b.get("sid") == "2074023-zone18.35-INGAME-S23"), None)
        self.assertIsNotNone(turret)
        self.assertFalse(turret["complete"])
        self.assertTrue(turret["is_paused"])
        self.assertFalse(turret["power_on"])
        self.assertGreater(turret["construction_progress_raw"], 1.0)

        # 3. Верстак робототехники / исследовательский верстак с питанием
        bench = next((b for b in all_buildings if b.get("sid") == "2577638-zone18.35-INGAME-S23"), None)
        self.assertIsNotNone(bench)
        self.assertTrue(bench["complete"])
        self.assertTrue(bench["power_on"])
        self.assertFalse(bench["is_paused"])
        self.assertFalse(bench["is_repeat"])
        self.assertGreater(len(bench.get("usage_nodes", [])), 0)

    @unittest.skip("LIVE-SLOT: состояние живого слота сохранения меняется во время игры; сквозная проверка выполняется в check_live.py и check_e2e_real.py")
    def test_real_save_storage_inventories(self) -> None:
        prop = read_player_property(self.save_dir, DEFAULT_PLAYER_FACTION)
        all_buildings = []
        for b in prop["buildings"]:
            all_buildings.append(b)
            all_buildings.extend(b.get("interior", []))

        all_items = [it for b in all_buildings for it in b.get("storage_items", [])]
        self.assertGreaterEqual(len(all_items), 200)

        # Наличие предметов с handle владельца
        items_with_owner = [it for it in all_items if it.get("owned_by") is not None]
        self.assertGreaterEqual(len(items_with_owner), 30)

        # 1. Запасы пеньки (sid: 1965-gamedata.base)
        hemp_items = [it for it in all_items if it.get("base_data_sid") == "1965-gamedata.base"]
        total_hemp = sum(it.get("quantity") or 1 for it in hemp_items)
        self.assertGreaterEqual(total_hemp, 500)

        # 2. Запасы медикаментов (sid: 1359-gamedata.base - Advanced First Aid Kit, 515-gamedata.base - Standard)
        med_items = [it for it in all_items if it.get("base_data_sid") in ("1359-gamedata.base", "515-gamedata.base")]
        total_meds = sum(it.get("quantity") or 1 for it in med_items)
        self.assertGreaterEqual(total_meds, 35)

        # 3. Древние научные книги (sid: 43953-rebirth.mod)
        ancient_books = [it for it in all_items if it.get("base_data_sid") == "43953-rebirth.mod"]
        total_books = sum(it.get("quantity") or 1 for it in ancient_books)
        self.assertGreaterEqual(total_books, 15)


if __name__ == "__main__":
    unittest.main()
