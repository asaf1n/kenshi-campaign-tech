"""Тесты сборки, схемы, детерминизма и географических инвариантов справочника мира."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import unittest

from tools.config import Config
from tools.io_utils import canonical, file_hash, read_json
from tools.validation import (
    WorldReferenceValidationError,
    check_privacy,
    validate,
    validate_world_reference,
)
from tools.world_reference import (
    build_world_reference,
    find_world_reference,
    save_world_reference,
)

ROOT = Path(__file__).resolve().parents[1]


class WorldReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = Config.from_env()
        cls.example_path = ROOT / "examples/world-reference.example.json"
        cls.example_data = read_json(cls.example_path.read_bytes())
        # Загружаем реальный справочник мира
        cls.world_ref = build_world_reference(cls.config)

    def test_schema_and_negative_contracts(self) -> None:
        """Валидация схемы Draft 2020-12 и семантические негативные сценарии."""
        validate(self.example_data, "world-reference")
        val_res = validate_world_reference(self.example_data)
        self.assertEqual(val_res["status"], "passed")

        # 1. Негативный: координата x выходит за диапазон [-147456, 147456)
        bad_coords = copy.deepcopy(self.example_data)
        bad_coords["locations"][0]["x"] = 150000.0
        with self.assertRaises(Exception):
            validate(bad_coords, "world-reference")

        bad_coords["locations"][0]["x"] = -150000.0
        with self.assertRaises(Exception):
            validate(bad_coords, "world-reference")

        # 2. Негативный: дублирующийся instance_id
        dup_id = copy.deepcopy(self.example_data)
        dup_id["locations"].append(copy.deepcopy(dup_id["locations"][0]))
        with self.assertRaises(WorldReferenceValidationError) as ctx:
            validate_world_reference(dup_id)
        self.assertIn("Дублирующийся instance_id", str(ctx.exception))

        # 3. Негативный: коллизия статического здания (одинаковые zone и building_id в разных локациях)
        dup_bldg = copy.deepcopy(self.example_data)
        dup_bldg["locations"][1]["static_buildings"] = [
            {"zone": "20.32", "building_id": 1}
        ]
        with self.assertRaises(WorldReferenceValidationError) as ctx:
            validate_world_reference(dup_bldg)
        self.assertIn("Коллизия статического здания", str(ctx.exception))

        # 4. Негативный: перекрывающиеся confirmed_radius
        overlap = copy.deepcopy(self.example_data)
        overlap["locations"][0]["confirmed_radius"] = 5000.0
        overlap["locations"][1]["confirmed_radius"] = 5000.0
        with self.assertRaises(WorldReferenceValidationError) as ctx:
            validate_world_reference(overlap)
        self.assertIn("Нарушение непересечения радиусов", str(ctx.exception))

        # 5. Негативный: сумма RLE не равна 65536
        bad_rle = copy.deepcopy(self.example_data)
        bad_rle["regions_map"]["rle"][0]["length"] = 65000
        with self.assertRaises(WorldReferenceValidationError) as ctx:
            validate_world_reference(bad_rle)
        self.assertIn("!= 65536", str(ctx.exception))

        # 5b. Публикуемая проекция без растра регионов и хешей исходников остаётся валидной
        from tools.current_layers import public_world_reference

        slim = public_world_reference(self.example_data)
        self.assertNotIn("regions_map", slim)
        self.assertNotIn("source_hashes", slim)
        validate_world_reference(slim)

        # 6. Негативный: ссылка на неизвестный регион
        bad_reg = copy.deepcopy(self.example_data)
        bad_reg["locations"][0]["region"] = "NonExistentRegion123"
        with self.assertRaises(WorldReferenceValidationError) as ctx:
            validate_world_reference(bad_reg)
        self.assertIn("ссылается на неизвестный регион", str(ctx.exception))

    def test_determinism_and_privacy(self) -> None:
        """AC-07, AC-10: Два прогона дают побайтово идентичный результат без путей пользователя."""
        second_ref = build_world_reference(self.config)
        self.assertEqual(self.world_ref["world_ref_id"], second_ref["world_ref_id"])
        raw_first = canonical(self.world_ref)
        raw_second = canonical(second_ref)
        self.assertEqual(raw_first, raw_second)

        # Проверка приватности: никаких локальных путей пользователя или секретов
        text = raw_first.decode("utf-8")
        check_privacy(text, "world-reference.json")
        self.assertNotIn("C:\\", text)
        self.assertNotIn("c:\\", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("C:/", text)

    def test_readonly_safety(self) -> None:
        """AC-02: Игра и моды остаются строго неизменными (read-only)."""
        game = self.config.game
        workshop = self.config.workshop

        # Проверяем, что хеши ключевых файлов игры совпадают с хешами в source_hashes
        source_hashes = self.world_ref["source_hashes"]
        base_hash = file_hash(game / "data/gamedata.base")
        self.assertEqual(source_hashes["data/gamedata.base"], base_hash)

        areasmap_hash = file_hash(game / "data/newland/land/areasmap.tga")
        self.assertEqual(source_hashes["data/newland/land/areasmap.tga"], areasmap_hash)

        po_hash = file_hash(game / "locale/ru_RU/gamedata.po")
        self.assertEqual(source_hashes["locale/ru_RU/gamedata.po"], po_hash)

    def test_sixteen_control_landmarks(self) -> None:
        """Сверка 16 контрольных ориентиров по всем частям мира."""
        landmarks = {
            "Squin",
            "The Hub",
            "Admag",
            "Mongrel",
            "World's End",
            "Burn's Tower",
            "Ancient Labs",
            "Flotsam Village",
            "Shark",
            "Grayflayer Village",
            "Flats Lagoon",
            "Smugglers Bar",
            "Stoat",
            "Catun",
            "Scout Post",
            "Throne of the Bugmaster",
        }

        locs_by_name: dict[str, list[dict]] = {}
        for loc in self.world_ref["locations"]:
            name = loc["name_en"]
            locs_by_name.setdefault(name, []).append(loc)

        for lm in landmarks:
            self.assertIn(lm, locs_by_name, f"Ориентир {lm} отсутствует в локациях мира")
            for loc in locs_by_name[lm]:
                self.assertTrue(loc["instance_id"])
                self.assertEqual(loc["coordinate_source"], "game_leveldata")
                self.assertGreaterEqual(loc["x"], -147456.0)
                self.assertLess(loc["x"], 147456.0)
                self.assertGreaterEqual(loc["z"], -147456.0)
                self.assertLess(loc["z"], 147456.0)
                self.assertRegex(loc["zone"], r"^\d{1,2}\.\d{1,2}$")
                self.assertIn(loc["radius_source"], {"game_buildings", "template_parameter"})
                self.assertGreater(loc["confirmed_radius"], 0.0)

        # Проверка 5 разных инстансов Ancient Labs (не склеены в один)
        ancient_labs = locs_by_name["Ancient Labs"]
        self.assertEqual(len(ancient_labs), 5)
        ancient_lab_ids = {loc["instance_id"] for loc in ancient_labs}
        self.assertEqual(len(ancient_lab_ids), 5)

        # Конкретные ориентиры и их ожидаемые регионы
        hub = locs_by_name["The Hub"][0]
        self.assertEqual(hub["instance_id"], "10-Newwworld-INGAME")
        self.assertEqual(hub["region"], "Border Zone")
        self.assertEqual(hub["radius_source"], "game_buildings")
        self.assertAlmostEqual(hub["confirmed_radius"], 2267.65, places=1)

        mongrel = locs_by_name["Mongrel"][0]
        self.assertEqual(mongrel["instance_id"], "3-Newwworld-INGAME")
        self.assertEqual(mongrel["region"], "Fog Islands")

        stoat = locs_by_name["Stoat"][0]
        self.assertEqual(stoat["instance_id"], "157-rebirth-INGAME")
        self.assertEqual(stoat["region"], "The Great Desert")

        smugglers = locs_by_name["Smugglers Bar"][0]
        self.assertEqual(smugglers["instance_id"], "113-rebirth-INGAME")
        self.assertEqual(smugglers["region"], "Shem")

    def test_pairwise_non_intersection_on_real_pairs(self) -> None:
        """Проверка попарного непересечения confirmed_radius на реальных парах."""
        locs = {loc["instance_id"]: loc for loc in self.world_ref["locations"]}

        # 1. The Hub и Rebel Base (нет конфликта, полный r_raw)
        hub = locs["10-Newwworld-INGAME"]
        rebel = locs["2-rebirth-INGAME"]
        d_hr = math.hypot(hub["x"] - rebel["x"], hub["z"] - rebel["z"])
        self.assertAlmostEqual(d_hr, 3977.1, places=1)
        self.assertAlmostEqual(hub["confirmed_radius"], 2267.65, places=1)
        self.assertAlmostEqual(rebel["confirmed_radius"], 202.06, places=1)
        self.assertLessEqual(hub["confirmed_radius"] + rebel["confirmed_radius"], 0.95 * d_hr)

        # 2. Trader's Edge и Heng (нет конфликта, r_raw = 350.0)
        trader = locs["116-rebirth-INGAME"]
        heng = locs["156-rebirth-INGAME"]
        d_th = math.hypot(trader["x"] - heng["x"], trader["z"] - heng["z"])
        self.assertAlmostEqual(d_th, 1882.5, places=1)
        self.assertAlmostEqual(trader["confirmed_radius"], 350.0, places=1)
        self.assertAlmostEqual(heng["confirmed_radius"], 350.0, places=1)
        self.assertLessEqual(trader["confirmed_radius"] + heng["confirmed_radius"], 0.95 * d_th)

        # 3. Black Desert City и Scraphouse (конфликт, пропорциональное деление)
        bdc = locs["51-Newwworld-INGAME"]
        scraphouse = locs["154-rebirth-INGAME"]
        d_bs = math.hypot(bdc["x"] - scraphouse["x"], bdc["z"] - scraphouse["z"])
        self.assertAlmostEqual(d_bs, 2561.9, places=1)
        self.assertAlmostEqual(bdc["confirmed_radius"], 1890.41, places=1)
        self.assertAlmostEqual(scraphouse["confirmed_radius"], 543.39, places=1)
        self.assertLessEqual(bdc["confirmed_radius"] + scraphouse["confirmed_radius"], 0.95 * d_bs + 1e-4)

        # 4. Skinhouse HQ и Barrier Tower (конфликт, пропорциональное деление)
        skinhouse = locs["198-rebirth-INGAME"]
        barrier = locs["292-rebirth-INGAME"]
        d_sb = math.hypot(skinhouse["x"] - barrier["x"], skinhouse["z"] - barrier["z"])
        self.assertAlmostEqual(d_sb, 5340.6, places=1)
        self.assertAlmostEqual(skinhouse["confirmed_radius"], 2558.34, places=1)
        self.assertAlmostEqual(barrier["confirmed_radius"], 2515.26, places=1)
        self.assertLessEqual(skinhouse["confirmed_radius"] + barrier["confirmed_radius"], 0.95 * d_sb + 1e-4)

    def test_radius_source_and_nest_markers_exclusion(self) -> None:
        """261 локация: 48 с game_buildings, 213 с template_parameter, 43 маркера отфильтровано."""
        locs = self.world_ref["locations"]
        self.assertEqual(len(locs), 261)
        self.assertEqual(self.world_ref["meta"]["total_locations"], 261)
        self.assertEqual(self.world_ref["meta"]["filtered_nest_markers"], 43)

        buildings_locs = [loc for loc in locs if loc["radius_source"] == "game_buildings"]
        template_locs = [loc for loc in locs if loc["radius_source"] == "template_parameter"]

        self.assertEqual(len(buildings_locs), 48)
        self.assertEqual(len(template_locs), 213)
        self.assertEqual(self.world_ref["meta"]["locations_with_buildings"], 48)

        # Ни один маркер спавна гнёзд не попал в локации
        for loc in locs:
            self.assertNotEqual(loc["kind"], "nest_marker")
            name_lower = loc["name_en"].lower()
            self.assertNotIn("marker-nest", name_lower)
            self.assertNotIn("spider camp marker", name_lower)

    def test_find_world_reference_and_mismatch(self) -> None:
        """Подбор справочника мира по mods и world_ref_id, выявление mismatch."""
        saved_path = save_world_reference(self.world_ref)
        self.assertTrue(saved_path.is_file())

        # 1. Поиск по точному world_ref_id
        path, warnings = find_world_reference(world_ref_id=self.world_ref["world_ref_id"])
        self.assertEqual(path, saved_path)
        self.assertEqual(warnings, [])

        # 2. Поиск по списку модов
        path, warnings = find_world_reference(mods=self.world_ref["mods"])
        self.assertEqual(path, saved_path)
        self.assertEqual(warnings, [])

        # 3. Несовпадение модов с world_ref_id
        path, warnings = find_world_reference(
            mods=["base", "NonExistentMod"],
            world_ref_id=self.world_ref["world_ref_id"],
        )
        self.assertEqual(path, saved_path)
        self.assertEqual(len(warnings), 1)
        self.assertIn("world_reference_mismatch", warnings[0])

        # 4. Отсутствующий world_ref_id
        path, warnings = find_world_reference(world_ref_id="sha256-nonexistent")
        self.assertIsNone(path)
        self.assertEqual(len(warnings), 1)
        self.assertIn("world_reference_mismatch", warnings[0])


if __name__ == "__main__":
    unittest.main()
