"""Тесты обогащения контекста: разделение животных, связь работ, техуровень и NPC."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.player_assets import research_block
from tools.rendering import render_brief, render_squad
from tools.world_context import aggregate_storage_by_location, enrich_world_and_production_context, storage_rows


class DummyGameNames:
    def __init__(self, data: dict | None = None):
        self.ix = data or {}

    def stats(self, sid: str) -> dict:
        return self.ix.get(sid, {})

    def type_of(self, sid: str) -> str:
        return self.ix.get(sid, {}).get("type", "RESEARCH")

    def name(self, sid: str) -> dict:
        entry = self.ix.get(sid, {})
        return {
            "sid": sid,
            "name": entry.get("name", sid),
            "name_ru": entry.get("name_ru", entry.get("name", sid)),
        }


class TestWorldContextAndProduction(unittest.TestCase):
    def test_aggregate_storage_by_location(self):
        buildings = [
            {
                "id": "b1",
                "name": "Склад",
                "town": "Ровная Лагуна",
                "storage_items": [
                    {"item_sid": "hemp", "name": "Пенька", "quantity": 50},
                ],
                "interior": [
                    {
                        "id": "b1_chest",
                        "name": "Ящик для руды",
                        "storage_items": [
                            {"item_sid": "iron", "name": "Железная руда", "quantity": 20},
                        ],
                    }
                ],
            },
            {
                "id": "b2",
                "name": "Дом в Скуине",
                "town": "Скуин",
                "storage": [
                    {"name": "Вяленое мясо", "quantity": 15},
                ],
            },
        ]
        res = aggregate_storage_by_location(buildings)
        self.assertIn("Ровная Лагуна", res)
        self.assertIn("Скуин", res)
        lagoon = res["Ровная Лагуна"]
        self.assertEqual(lagoon["buildings_count"], 2)  # здание + интерьер
        self.assertEqual(lagoon["total_items_count"], 70)
        items_map = {it["name"]: it["quantity"] for it in lagoon["items_summary"]}
        self.assertEqual(items_map["Пенька"], 50)
        self.assertEqual(items_map["Железная руда"], 20)

    def test_storage_rows_with_and_without_settlement(self):
        # Ловит потерю предметов у зданий без поселения и подмену имён предметов их SID.
        def building(bid, zone, qty, town=None, interior=()):
            return {"id": bid, "zone": zone, "name": f"Склад {bid}", "town": town, "interior": list(interior),
                    "storage": [{"sid": "hemp-sid", "name": "Пенька", "quantity": qty}],
                    "storage_items": [{"base_data_sid": "hemp-sid", "quantity": qty, "container": f"storage>c-{bid}"}]}

        buildings = [
            building("b1", "24.40", 10, interior=[building("b1-in", "24.40", 5)]),
            building("b2", "35.42", 7),
            building("b3", "19.35", 3),
        ]
        locations = {"buildings": [
            {"building_key": "24.40:b1", "location": {"name_ru": "Деревня"}, "nearby_locations": []},
            {"building_key": "35.42:b2", "location": None, "nearby_locations": [{"name_ru": "Лагуна"}]},
        ]}
        index = {"hemp-sid": ["Hemp", "Пенька", "ITEM", "base", {"value": 100, "value_source": "rebirth.mod"}]}
        rows, _ = storage_rows(buildings, locations, index)
        self.assertEqual({r["item"] for r in rows}, {"Пенька"})
        self.assertEqual({(r["building_id"], r["settlement"], r["settlement_status"], r["base_price"]) for r in rows}, {
            ("b1", "Деревня", "in_location", 100), ("b1-in", "Деревня", "in_location", 100),
            ("b2", "Лагуна", "near_location", 100), ("b3", None, "zone", 100)})
        result = aggregate_storage_by_location(buildings, locations, index)
        self.assertEqual({k: v["total_items_count"] for k, v in result.items()},
                         {"Деревня": 15, "Лагуна": 7, "зона 19.35 (поселение не определено)": 3})
        self.assertEqual(sum(v["total_items_count"] for v in result.values()), sum(r["quantity"] for r in rows))

    def test_enrich_jobs_carrying_and_npcs(self):
        chars = [
            {
                "id": "p1",
                "name": "Хот Лон",
                "handle": [1, 10, 20, 1, 100],
                "carrying": {
                    "is_carrying": True,
                    "target_handle": [1, 99, 88, 1, 777],
                },
                "jobs": {
                    "primary": [
                        {
                            "task_code": 10,
                            "task_name": "OPERATE_STORAGE",
                            "target_handle": [0, 50, 60, 2, 500],
                        },
                        {
                            "task_code": 20,
                            "task_name": "BODYGUARD",
                            "target_handle": [1, 20, 30, 1, 200],
                        },
                    ],
                    "temporary": [],
                },
            },
            {
                "id": "p2",
                "name": "Мью",
                "handle": [1, 20, 30, 1, 200],
                "carrying": {
                    "is_carrying": False,
                    "target_handle": None,
                },
                "jobs": {"primary": [], "temporary": []},
            },
        ]
        prop_data = {
            "buildings": [
                {
                    "id": "bld_1",
                    "name": "База Шельф",
                    "handle": [0, 50, 60, 1, 400],
                    "interior": [
                        {
                            "id": "box_1",
                            "name": "Контейнер конопли",
                            "handle": [0, 50, 60, 2, 500],
                        }
                    ],
                }
            ]
        }
        npcs = [
            {
                "index": 0,
                "sid": "phoenix",
                "name": "Holy Lord Phoenix",
                "state_code": 2,
                "player": True,
                "handle": [1, 5, 5, 1, 55],
            },
            {
                "index": 1,
                "sid": "shade",
                "name": "Shade",
                "name_ru": "Тень",
                "state_code": 2,
                "player": True,
                "handle": [1, 99, 88, 1, 777],
            },
            {
                "index": 2,
                "sid": "neutral_boss",
                "name": "Unknown Bandit",
                "state_code": 2,
                "player": False,
                "handle": [1, 7, 7, 1, 77],
            },
        ]

        enrich_world_and_production_context(chars, prop_data, npcs)

        # 1. Проверяем обогащение jobs
        p_tasks = chars[0]["jobs"]["primary"]
        self.assertEqual(p_tasks[0]["target_name"], "Контейнер конопли (База Шельф)")
        self.assertEqual(p_tasks[1]["target_name"], "Мью")

        # 2. Проверяем обогащение carrying (цель [1, 99, 88, 1, 777] разрешена как уникальный NPC "Тень")
        self.assertEqual(chars[0]["carrying"]["target_name"], "Тень")

        # 3. Проверяем статус уникальных NPC
        # Shade несётся Хот Лоном
        self.assertEqual(npcs[1]["captured_by"], "Хот Лон")
        # Интерпретации состояния не добавляются; у не несомых NPC нет носителя
        for npc in npcs:
            self.assertNotIn("state_detail", npc)
        self.assertNotIn("captured_by", npcs[0])
        self.assertNotIn("captured_by", npcs[2])

        # 4. Проверяем создание storage_by_location
        self.assertIn("storage_by_location", prop_data)

    def test_tech_level_calculation(self):
        names_data = {
            "tech_lvl_2": {"type": "RESEARCH", "is level upgrade": True, "level": 1, "name": "Tech Level 2"},
            "tech_lvl_3": {"type": "RESEARCH", "is level upgrade": True, "level": 2, "name": "Tech Level 3"},
            "sword_blueprint": {"type": "RESEARCH", "blueprint only": True, "name": "Blueprint: Katana"},
        }
        names = DummyGameNames(names_data)
        raw_res = {
            "finished": ["tech_lvl_2", "tech_lvl_3", "sword_blueprint"],
            "paid": ["tech_lvl_2", "tech_lvl_3", "sword_blueprint"],
            "current": [],
            "current_progress": [],
            "num_finished": 3,
            "num_currents": 0,
        }
        res = research_block(raw_res, names, [])
        self.assertEqual(res["tech_level"], 3)
        self.assertIn("Blueprint: Katana", res["blueprints_completed"])
        self.assertIn("Tech Level 2", res["level_upgrades_completed"])
        self.assertIn("Tech Level 3", res["level_upgrades_completed"])

    def test_rendering_animals_and_context(self):
        current = {
            "world": {"day": 129, "time": "12:00", "money": 50000},
            "characters": [
                {
                    "id": "c1",
                    "name": "Хот Лон",
                    "squad_id": "sq1",
                    "race": "Скелет",
                    "is_animal": False,
                    "jobs": {
                        "primary": [{"task_name": "OPERATE_STORAGE", "target_name": "Ящик руды"}]
                    },
                    "carrying": {
                        "is_carrying": True,
                        "target_name": "Раненый пёс",
                    },
                },
                {
                    "id": "c2",
                    "name": "Барбос",
                    "squad_id": "sq1",
                    "race": "Костепёс",
                    "is_animal": True,
                },
            ],
            "squads": [{"squad_id": "sq1", "squad_name": "Штурм"}],
            "property": {
                "storage_by_location": {
                    "Ровная Лагуна": {
                        "buildings_count": 1,
                        "total_items_count": 100,
                        "items_summary": [{"name": "Пенька", "quantity": 100}],
                    }
                }
            },
            "world_state": {
                "unique_npcs": [
                    {
                        "sid": "shade",
                        "name_ru": "Тень",
                        "state_code": 2,
                        "captured_by": "Хот Лон",
                    }
                ]
            },
        }

        brief = render_brief(current, {}, "test_snap")
        self.assertIn("гуманоидов: 1, животных: 1", brief)
        self.assertIn("Складской баланс по базам и поселениям", brief)
        self.assertIn("Ровная Лагуна", brief)
        self.assertIn("Тень (код 2 — несёт Хот Лон)", brief)

        squad_md = render_squad(current, "test_snap")
        self.assertIn("### Барбос (`c2`) [Животное]", squad_md)
        self.assertIn("OPERATE_STORAGE [Ящик руды]", squad_md)
        self.assertIn("- **Переноска:** несёт Раненый пёс", squad_md)


if __name__ == "__main__":
    unittest.main()
