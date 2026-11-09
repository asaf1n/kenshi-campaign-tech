"""Тесты этапа 2.2: пространственное разрешение локаций (kenshi-locations/1),
группы близости, перемещения и интеграция в публикацию.
"""
from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import shutil
from tools.config import Config
from tools.io_utils import canonical, read_json
from tools.locator import (
    parse_3d_coordinates,
    resolve_locations,
)
from tools.publisher import PublisherError, publish
from tools.validation import (
    validate,
    validate_locations,
)
from tools.world_reference import build_world_reference

ROOT = Path(__file__).resolve().parents[1]
REAL_CONVERTER_SESSIONS = Path("C:/Users/artem/kenshi-platoon-export/sessions")


class LocationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = Config.from_env()
        cls.world_ref = build_world_reference(cls.config)
        cls.example_locations = read_json((ROOT / "examples/locations.example.json").read_bytes())
        # Найдем реальный снимок
        snapshots_dir = ROOT / "data/snapshots"
        cls.sample_snapshot_dir = None
        if snapshots_dir.is_dir():
            for p in snapshots_dir.glob("sha256-*"):
                if (p / "current.json").is_file():
                    cls.sample_snapshot_dir = p
                    break

    def test_schema_and_example_validation(self) -> None:
        """Валидация схемы contracts/locations.schema.json на examples/locations.example.json."""
        validate(self.example_locations, "locations")
        example_copy = dict(self.example_locations)
        example_copy["world_ref_id"] = self.world_ref["world_ref_id"]
        res = validate_locations(example_copy, world_ref=self.world_ref)
        self.assertEqual(res["status"], "passed")
        self.assertEqual(res["snapshot_id"], self.example_locations["snapshot_id"])

    def test_coordinate_parsing_strictness(self) -> None:
        """Строгий разбор 3D-координат и негативные сценарии (→ None)."""
        # Валидные 3-элементные списки и кортежи
        self.assertEqual(parse_3d_coordinates([10.5, 20.0, -30.2]), (10.5, 20.0, -30.2))
        self.assertEqual(parse_3d_coordinates((0, 0, 0)), (0.0, 0.0, 0.0))
        # Валидные словари
        self.assertEqual(parse_3d_coordinates({"x": 100.0, "z": -200.0}), (100.0, 0.0, -200.0))
        self.assertEqual(parse_3d_coordinates({"x": 100.0, "y": 15.0, "z": -200.0}), (100.0, 15.0, -200.0))

        # Негативные: неверная размерность
        self.assertIsNone(parse_3d_coordinates([10.0, 20.0]))  # 2D массив [x, y]
        self.assertIsNone(parse_3d_coordinates([10.0]))
        self.assertIsNone(parse_3d_coordinates([]))
        self.assertIsNone(parse_3d_coordinates([1.0, 2.0, 3.0, 4.0]))

        # Негативные: неверные типы (bool, str, None)
        self.assertIsNone(parse_3d_coordinates([True, 10.0, 20.0]))
        self.assertIsNone(parse_3d_coordinates([10.0, False, 20.0]))
        self.assertIsNone(parse_3d_coordinates(["10.0", 20.0, 30.0]))
        self.assertIsNone(parse_3d_coordinates(None))
        self.assertIsNone(parse_3d_coordinates("string"))

        # Негативные: NaN, Inf
        self.assertIsNone(parse_3d_coordinates([float("nan"), 10.0, 20.0]))
        self.assertIsNone(parse_3d_coordinates([10.0, float("inf"), 20.0]))
        self.assertIsNone(parse_3d_coordinates({"x": float("nan"), "z": 10.0}))

        # Негативные: выход за границы [-147456, 147456)
        self.assertIsNone(parse_3d_coordinates([-147456.1, 0.0, 0.0]))
        self.assertIsNone(parse_3d_coordinates([147456.0, 0.0, 0.0]))
        self.assertIsNone(parse_3d_coordinates([0.0, 0.0, -147457.0]))
        self.assertIsNone(parse_3d_coordinates([0.0, 0.0, 150000.0]))

    def test_four_resolution_statuses_for_characters(self) -> None:
        """Проверка ровно четырёх статусов разрешения для персонажей."""
        squin = [l for l in self.world_ref["locations"] if l["name_en"] == "Squin"][0]
        hub = [l for l in self.world_ref["locations"] if l["name_en"] == "The Hub"][0]
        # Деревня Серодёров имеет radius_source = "template_parameter"
        grayflayer = [l for l in self.world_ref["locations"] if l["name_en"] == "Grayflayer Village"][0]
        self.assertEqual(grayflayer["radius_source"], "template_parameter")

        test_current = {
            "format": "kenshi-session/10",
            "characters": [
                # 1. in_location: внутри подтверждённой застройки Squin (game_buildings)
                {
                    "id": "c1",
                    "name": "В городе",
                    "squad_id": "sq",
                    "position": [squin["x"] + 100.0, squin["y"], squin["z"] + 100.0],
                },
                # 2. near_location: внутри радиуса шаблона Grayflayer Village (template_parameter)
                # Персонажи в template_parameter НЕ получают in_location
                {
                    "id": "c2",
                    "name": "В деревне шаблона",
                    "squad_id": "sq",
                    "position": [grayflayer["x"] + 50.0, grayflayer["y"], grayflayer["z"] + 50.0],
                },
                # 3. in_region: вдали от всех городов в регионе Border Zone
                {
                    "id": "c3",
                    "name": "В глуши",
                    "squad_id": "sq",
                    "position": [-50000.0, 0.0, 10000.0],
                },
                # 4. unknown: невалидные координаты
                {
                    "id": "c4",
                    "name": "Аномалия",
                    "squad_id": "sq",
                    "position": [float("nan"), 0.0, 0.0],
                },
            ],
            "property": {"buildings": []},
        }

        res = resolve_locations(test_current, self.world_ref, snapshot_id="test-snap")
        chars = {c["id"]: c for c in res["characters"]}

        # c1 -> in_location
        self.assertEqual(chars["c1"]["resolution_status"], "in_location")
        self.assertIsNotNone(chars["c1"]["location"])
        self.assertEqual(chars["c1"]["location"]["instance_id"], squin["instance_id"])

        # c2 -> near_location (из-за radius_source == "template_parameter")
        self.assertEqual(chars["c2"]["resolution_status"], "near_location")
        self.assertIsNone(chars["c2"]["location"])
        self.assertTrue(len(chars["c2"]["nearby_locations"]) > 0)
        self.assertEqual(chars["c2"]["nearby_locations"][0]["instance_id"], grayflayer["instance_id"])

        # c3 -> in_region
        self.assertEqual(chars["c3"]["resolution_status"], "in_region")
        self.assertIsNone(chars["c3"]["location"])
        self.assertEqual(chars["c3"]["nearby_locations"], [])
        self.assertIsNotNone(chars["c3"]["region"])

        # c4 -> unknown
        self.assertEqual(chars["c4"]["resolution_status"], "unknown")
        self.assertIsNone(chars["c4"]["location"])
        self.assertIsNone(chars["c4"]["region"])
        self.assertEqual(chars["c4"]["nearby_locations"], [])
        self.assertTrue(len(chars["c4"]["warnings"]) > 0)

    def test_building_identity_and_resolution_rules(self) -> None:
        """Проверка составного ключа (zone, id) и правил (а), (б) и чисто пространственного разрешения."""
        squin = [l for l in self.world_ref["locations"] if l["name_en"] == "Squin"][0]
        grayflayer = [l for l in self.world_ref["locations"] if l["name_en"] == "Grayflayer Village"][0]

        # 1. Отказ при дубликате составного ключа
        dup_current = {
            "characters": [],
            "property": {
                "buildings": [
                    {"id": "b1", "zone": "18.35", "position": [-62000.0, 0.0, 16000.0]},
                    {"id": "b1", "zone": "18.35", "position": [-62100.0, 0.0, 16100.0]},
                ]
            },
        }
        with self.assertRaises(ValueError) as ctx:
            resolve_locations(dup_current, self.world_ref, snapshot_id="test-dup")
        self.assertIn("дубликат составного ключа", str(ctx.exception))

        # 2. Отказ при пустом id постройки
        bad_id_current = {
            "characters": [],
            "property": {
                "buildings": [
                    {"id": "", "zone": "18.35", "position": [-62000.0, 0.0, 16000.0]},
                ]
            },
        }
        with self.assertRaises(ValueError) as ctx:
            resolve_locations(bad_id_current, self.world_ref, snapshot_id="test-empty-id")
        self.assertIn("отсутствует или пуст 'id'", str(ctx.exception))

        # 3. Отсутствующий zone -> "unknown:<id>" с вычислением зоны по координатам и warning
        missing_zone_current = {
            "characters": [],
            "property": {
                "buildings": [
                    {"id": "camp1", "zone": None, "position": [-70387.3, 0.0, 24497.2]},
                ]
            },
        }
        res_mz = resolve_locations(missing_zone_current, self.world_ref, snapshot_id="test-mz")
        b_mz = res_mz["buildings"][0]
        self.assertEqual(b_mz["building_key"], "unknown:camp1")
        self.assertEqual(b_mz["zone"], "16.37")  # вычислена по координатам
        self.assertTrue(any("отсутствует поле 'zone'" in w for w in b_mz["warnings"]))

        # 4. Правило (б): подтверждение именем building.town внутри confirmed_radius template_parameter
        # Дома в «Деревне Серодёров» 24.40 на d=238.1 от центра 89-rebirth-INGAME
        gf_current = {
            "characters": [],
            "property": {
                "buildings": [
                    {
                        "id": "12-Newwworld-INGAME",
                        "zone": "24.40",
                        "town": "Деревня Серодёров",
                        "position": [-34601.99 + 150.0, 0.0, 40161.89 + 150.0],
                    },
                    # Постройка с town = null внутри радиуса шаблона за пределами застройки ожидает near_location
                    {
                        "id": "no-town-in-gf",
                        "zone": "24.40",
                        "town": None,
                        "position": [-34601.99 + 150.0, 0.0, 40161.89 + 150.0],
                    },
                    # Постройка с несовпадающим town внутри радиуса застройки ожидает near_location
                    {
                        "id": "wrong-town-in-squin",
                        "zone": "18.35",
                        "town": "Стопка",  # не Скуин
                        "position": [squin["x"] + 100.0, squin["y"], squin["z"] + 100.0],
                    },
                    # Пользовательский форпост в глуши (не получает чужой instance_id)
                    {
                        "id": "my-outpost-hq",
                        "zone": "20.30",
                        "town": "Моя Крепость",
                        "position": [-50000.0, 0.0, 10000.0],
                    },
                ]
            },
        }
        res_b = resolve_locations(gf_current, self.world_ref, snapshot_id="test-gf")
        b_map = {b["id"]: b for b in res_b["buildings"]}

        # 12-Newwworld-INGAME -> in_location (по правилу б)
        self.assertEqual(b_map["12-Newwworld-INGAME"]["resolution_status"], "in_location")
        self.assertEqual(b_map["12-Newwworld-INGAME"]["location"]["instance_id"], grayflayer["instance_id"])

        # no-town-in-gf -> near_location
        self.assertEqual(b_map["no-town-in-gf"]["resolution_status"], "near_location")
        self.assertIsNone(b_map["no-town-in-gf"]["location"])

        # wrong-town-in-squin -> near_location
        self.assertEqual(b_map["wrong-town-in-squin"]["resolution_status"], "near_location")
        self.assertIsNone(b_map["wrong-town-in-squin"]["location"])
        self.assertTrue(any("не совпадает" in w for w in b_map["wrong-town-in-squin"]["warnings"]))

        # my-outpost-hq -> in_region, сохраняет save_town_name, чужой instance_id не присваивается
        self.assertEqual(b_map["my-outpost-hq"]["resolution_status"], "in_region")
        self.assertEqual(b_map["my-outpost-hq"]["save_town_name"], "Моя Крепость")
        self.assertIsNone(b_map["my-outpost-hq"]["location"])

    def test_same_name_characters_and_locations(self) -> None:
        """AC-04: Участники с одинаковыми именами не склеиваются, одноимённые локации имеют разные instance_id."""
        ancient_labs = [l for l in self.world_ref["locations"] if l["name_en"] == "Ancient Labs"]
        self.assertGreaterEqual(len(ancient_labs), 2)
        lab1 = ancient_labs[0]
        lab2 = ancient_labs[1]
        self.assertNotEqual(lab1["instance_id"], lab2["instance_id"])

        # Два одноимённых вьючных быка в разных местах
        two_bulls_current = {
            "characters": [
                {
                    "id": "ps:bull-1",
                    "name": "Вьючный бык",
                    "squad_id": "Base",
                    "position": [lab1["x"], lab1["y"], lab1["z"]],
                },
                {
                    "id": "ps:bull-2",
                    "name": "Вьючный бык",
                    "squad_id": "Cooperate",
                    "position": [lab2["x"], lab2["y"], lab2["z"]],
                },
            ],
            "property": {"buildings": []},
        }

        res = resolve_locations(two_bulls_current, self.world_ref, snapshot_id="bulls-test")
        self.assertEqual(len(res["characters"]), 2)
        c1, c2 = res["characters"][0], res["characters"][1]
        self.assertEqual(c1["name"], "Вьючный бык")
        self.assertEqual(c2["name"], "Вьючный бык")
        self.assertNotEqual(c1["id"], c2["id"])

        # Пространственные группы разделили быков в разные группы, несмотря на общее имя
        self.assertEqual(len(res["spatial_groups"]), 2)
        group_members = [g["characters"][0]["id"] for g in res["spatial_groups"]]
        self.assertIn("ps:bull-1", group_members)
        self.assertIn("ps:bull-2", group_members)

    def test_spatial_groups_and_movement(self) -> None:
        """Детерминированная кластеризация spatial_groups и расчёт movement."""
        squin = [l for l in self.world_ref["locations"] if l["name_en"] == "Squin"][0]
        hub = [l for l in self.world_ref["locations"] if l["name_en"] == "The Hub"][0]

        # Предыдущий снимок: Чарли и Боб в Скуине
        prev_current = {
            "snapshot_id": "snap-prev",
            "characters": [
                {"id": "c1", "name": "Боб", "squad_id": "sq", "position": [squin["x"], squin["y"], squin["z"]]},
                {"id": "c2", "name": "Чарли", "squad_id": "sq", "position": [squin["x"] + 10, squin["y"], squin["z"]]},
            ],
            "property": {"buildings": []},
        }
        prev_locations = resolve_locations(prev_current, self.world_ref, snapshot_id="snap-prev")

        # Текущий снимок: Боб переместился в Хаб, Чарли остался в Скуине
        cur_current = {
            "snapshot_id": "snap-cur",
            "characters": [
                {"id": "c1", "name": "Боб", "squad_id": "sq", "position": [hub["x"], hub["y"], hub["z"]]},
                {"id": "c2", "name": "Чарли", "squad_id": "sq", "position": [squin["x"] + 10, squin["y"], squin["z"]]},
            ],
            "property": {"buildings": []},
        }

        # 1. При diff.valid == False -> movement.valid == False
        invalid_diff = {"valid": False, "reason": "нет baseline для сравнения"}
        res_invalid_mov = resolve_locations(
            cur_current,
            self.world_ref,
            snapshot_id="snap-cur",
            previous_locations=prev_locations,
            changes=invalid_diff,
        )
        self.assertFalse(res_invalid_mov["movement"]["valid"])
        self.assertEqual(res_invalid_mov["movement"]["characters"], [])

        # 2. При diff.valid == True -> расчёт прямолинейного 2D смещения
        valid_diff = {
            "valid": True,
            "from": {"snapshot_id": "snap-prev"},
            "to": {"snapshot_id": "snap-cur"},
        }
        res_valid_mov = resolve_locations(
            cur_current,
            self.world_ref,
            snapshot_id="snap-cur",
            previous_locations=prev_locations,
            changes=valid_diff,
        )
        mov = res_valid_mov["movement"]
        self.assertTrue(mov["valid"])
        self.assertEqual(len(mov["characters"]), 2)

        bob_move = [m for m in mov["characters"] if m["character_id"] == "c1"][0]
        self.assertTrue(bob_move["is_straight_line"])
        expected_dist = round(math.hypot(hub["x"] - squin["x"], hub["z"] - squin["z"]), 1)
        self.assertAlmostEqual(bob_move["distance_2d"], expected_dist, delta=1.0)
        self.assertEqual(bob_move["from_location"], "Squin")
        self.assertEqual(bob_move["to_location"], "The Hub")

        charlie_move = [m for m in mov["characters"] if m["character_id"] == "c2"][0]
        self.assertEqual(charlie_move["distance_2d"], 0.0)

        # 3. Пространственные группы: разделены на Скуин и Хаб
        self.assertEqual(len(res_valid_mov["spatial_groups"]), 2)
        group_labels = {g["label"] for g in res_valid_mov["spatial_groups"]}
        self.assertIn("Скуин", group_labels)
        self.assertIn("Хаб", group_labels)

    def test_real_historical_session_ps621948224(self) -> None:
        """Сверка реального персонажа ps:621948224 в Скуине (d=1549) по sessions/2026-11-01_12-21-35.json."""
        session_file = REAL_CONVERTER_SESSIONS / "2026-11-01_12-21-35.json"
        if not session_file.is_file():
            self.skipTest(f"Исторический файл сессии не найден: {session_file}")

        sess_data = read_json(session_file.read_bytes())
        res = resolve_locations(sess_data, self.world_ref, snapshot_id="hist-test")

        chars_by_id = {c["id"]: c for c in res["characters"]}
        self.assertIn("ps:621948224", chars_by_id)
        target = chars_by_id["ps:621948224"]

        # Должен быть строго in_location в Скуине
        self.assertEqual(target["resolution_status"], "in_location")
        self.assertIsNotNone(target["location"])
        self.assertEqual(target["location"]["name_en"], "Squin")
        self.assertAlmostEqual(target["location"]["distance"], 1549.2, places=0)

    def test_real_snapshot_buildings_and_platforms(self) -> None:
        """Сверка купленных домов в Скуине, Хабе, Серодёрах и платформы в Ровной Лагуне (zone 35.42 при центре 36.42)."""
        if self.sample_snapshot_dir is None:
            self.skipTest("Снимок data/snapshots не найден")

        cur_data = read_json((self.sample_snapshot_dir / "current.json").read_bytes())
        res = resolve_locations(cur_data, self.world_ref, snapshot_id="real-snapshot")
        val = validate_locations(res, world_ref=self.world_ref)
        self.assertEqual(val["status"], "passed")

        bldgs = {b["id"]: b for b in res["buildings"]}

        # Скуин: 23-rebirth-INGAME -> in_location в Скуине
        self.assertIn("23-rebirth-INGAME", bldgs)
        self.assertEqual(bldgs["23-rebirth-INGAME"]["resolution_status"], "in_location")
        self.assertEqual(bldgs["23-rebirth-INGAME"]["location"]["name_en"], "Squin")

        # Хаб: 32-Newwworld-INGAME -> in_location в Хабе
        self.assertIn("32-Newwworld-INGAME", bldgs)
        self.assertEqual(bldgs["32-Newwworld-INGAME"]["resolution_status"], "in_location")
        self.assertEqual(bldgs["32-Newwworld-INGAME"]["location"]["name_en"], "The Hub")

        # Серодёры: 12-Newwworld-INGAME -> in_location в Grayflayer Village
        self.assertIn("12-Newwworld-INGAME", bldgs)
        self.assertEqual(bldgs["12-Newwworld-INGAME"]["resolution_status"], "in_location")
        self.assertEqual(bldgs["12-Newwworld-INGAME"]["location"]["name_en"], "Grayflayer Village")

        # Платформа в Ровной Лагуне: 46-Newwworld-INGAME, zone="35.42", центр лагуны в 36.42
        self.assertIn("46-Newwworld-INGAME", bldgs)
        flats_bldg = bldgs["46-Newwworld-INGAME"]
        self.assertEqual(flats_bldg["zone"], "35.42")
        self.assertEqual(flats_bldg["resolution_status"], "in_location")
        self.assertEqual(flats_bldg["location"]["name_en"], "Flats Lagoon")
        self.assertAlmostEqual(flats_bldg["location"]["distance"], 916.7, places=0)

    def test_fault_injection_and_atomic_rollback(self) -> None:
        """AC-08: fault injection на гео-этапе публикации оставляет предыдущий release bundle и .gitkeep байтово нетронутыми."""
        if self.sample_snapshot_dir is None:
            self.skipTest("Снимок data/snapshots не найден")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            pub_dir = tmp / "published"
            pub_dir.mkdir()
            (pub_dir / ".gitkeep").write_bytes(b"GITKEEP_CONTENT")

            snap_id = self.sample_snapshot_dir.name
            snapshots_dir = tmp / "snapshots"
            shutil.copytree(self.sample_snapshot_dir, snapshots_dir / snap_id)
            diffs_dir = tmp / "diffs"
            test_idx = tmp / "index.json"

            # 1. Первая успешная публикация
            res1 = publish(
                snapshot_id=snap_id,
                snapshots_dir=snapshots_dir,
                diffs_dir=diffs_dir,
                published_dir=pub_dir,
                world_ref_id=self.world_ref["world_ref_id"],
                index_path=test_idx,
            )
            self.assertEqual(res1["status"], "published")
            first_manifest = (pub_dir / "manifest.json").read_bytes()
            first_gitkeep = (pub_dir / ".gitkeep").read_bytes()
            self.assertEqual(first_gitkeep, b"GITKEEP_CONTENT")

            # 2. Сбой при попытке повторной публикации с fault_inject="geo_error"
            with self.assertRaises(PublisherError):
                publish(
                    snapshot_id=snap_id,
                    snapshots_dir=snapshots_dir,
                    diffs_dir=diffs_dir,
                    published_dir=pub_dir,
                    world_ref_id=self.world_ref["world_ref_id"],
                    index_path=test_idx,
                    fault_inject="geo_error",
                )

            # Проверка байтовой сохранности предыдущего релиза
            self.assertEqual((pub_dir / "manifest.json").read_bytes(), first_manifest)
            self.assertEqual((pub_dir / ".gitkeep").read_bytes(), b"GITKEEP_CONTENT")

    def test_hashseed_determinism(self) -> None:
        """AC-07: Запуски с разным PYTHONHASHSEED дают побайтово идентичные locations.json."""
        if self.sample_snapshot_dir is None:
            self.skipTest("Снимок data/snapshots не найден")

        cur_data = read_json((self.sample_snapshot_dir / "current.json").read_bytes())
        res1 = resolve_locations(cur_data, self.world_ref, snapshot_id="det-test", generated_at="2026-11-01T20:00:00Z")
        raw1 = canonical(res1)

        # Выполняем в дочернем процессе с другим seed через stdin
        code = (
            "import os, sys, json; from tools.locator import resolve_locations; from tools.io_utils import canonical, read_json; "
            "req = read_json(sys.stdin.buffer.read()); "
            "res = resolve_locations(req['cur'], req['w'], snapshot_id='det-test', generated_at='2026-11-01T20:00:00Z'); "
            "sys.stdout.buffer.write(canonical(res))"
        )

        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "99999"
        sub = subprocess.run(
            [sys.executable, "-c", code],
            input=canonical({"cur": cur_data, "w": self.world_ref}),
            stdout=subprocess.PIPE,
            check=True,
            env=env,
        )
        self.assertEqual(raw1, sub.stdout)


if __name__ == "__main__":
    unittest.main()
