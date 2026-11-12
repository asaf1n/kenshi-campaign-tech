"""Unit-тесты для модуля tools/skills_history.py.

Проверяют:
1. Выборку строго последних снимков дня через select_daily_snapshots.
2. Устойчивость к перезагрузкам (reload resilience) через campaign_chain.
3. Дельта-трекинг целочисленных уровней навыков/атрибутов (skills_history.csv).
4. Динамику казны и численности отряда (campaign_history.csv).
5. Историю дипломатических отношений с фракциями (relations_history.csv).
6. Загрузку снимков из директории (load_daily_snapshots, load_daily_snapshots_from_dir).
7. Детерминированность вывода (побайтовая идентичность CSV).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from typing import Any

from tools.io_utils import canonical, read_json
from tools.skills_history import (
    TRACKED_SKILLS,
    build_campaign_history,
    build_relations_history,
    build_skills_history,
    load_daily_snapshots,
    load_daily_snapshots_from_dir,
    select_daily_snapshots,
)
from tools.tables import ATTRIBUTES, SKILL_LABELS, csv_bytes


class TestSkillsHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.maxDiff = None

    def test_tracked_skills_coverage(self) -> None:
        """Все 4 атрибута и все 33 навыка/атрибута из SKILL_LABELS входят в TRACKED_SKILLS."""
        for attr in ATTRIBUTES:
            self.assertIn(attr, TRACKED_SKILLS)
        for skill in SKILL_LABELS:
            self.assertIn(skill, TRACKED_SKILLS)
        self.assertEqual(len(TRACKED_SKILLS), 33)

    def test_select_daily_snapshots(self) -> None:
        """Общая проверка select_daily_snapshots: дедупликация внутри дня и откат перезагрузки."""
        # Кампания с несколькими снимками в один день и откатом
        index_data = {
            "format": "kenshi-index/2",
            "campaigns": {"c1": {"head_snapshot_id": "s4"}},
            "snapshots": {
                "s1": {
                    "snapshot_id": "s1",
                    "campaign_id": "c1",
                    "parent_snapshot_id": None,
                    "game_time": {"day": 10, "time": "08:00"},
                },
                "s2": {
                    "snapshot_id": "s2",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s1",
                    "game_time": {"day": 10, "time": "20:00"},
                },
                "s3": {
                    "snapshot_id": "s3",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s2",
                    "game_time": {"day": 11, "time": "12:00"},
                },
                "s4": {
                    "snapshot_id": "s4",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s3",
                    "game_time": {"day": 12, "time": "06:00"},
                },
            },
        }
        daily = select_daily_snapshots(index_data, "s4")
        self.assertEqual(daily, [(10, "s2"), (11, "s3"), (12, "s4")])

    def test_select_daily_snapshots_deduplication(self) -> None:
        """Несколько снимков за один день сводятся строго к последнему снимку этого дня."""
        index_data = {
            "format": "kenshi-index/2",
            "campaigns": {"c1": {"head_snapshot_id": "s3"}},
            "snapshots": {
                "s1": {
                    "snapshot_id": "s1",
                    "campaign_id": "c1",
                    "parent_snapshot_id": None,
                    "game_time": {"day": 1, "time": "06:30"},
                },
                "s2": {
                    "snapshot_id": "s2",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s1",
                    "game_time": {"day": 1, "time": "14:15"},
                },
                "s3": {
                    "snapshot_id": "s3",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s2",
                    "game_time": {"day": 1, "time": "23:45"},
                },
            },
        }
        daily = select_daily_snapshots(index_data, "s3")
        self.assertEqual(daily, [(1, "s3")])

    def test_select_daily_snapshots_reload_resilience(self) -> None:
        """Снимки, отменённые откатом игрового времени (перезагрузкой), корректно отфильтровываются."""
        # s1 (День 5 10:00) -> s2 (День 6 12:00) -> s3 (День 5 14:00, откат/перезагрузка на день 5)
        index_data = {
            "format": "kenshi-index/2",
            "campaigns": {"c1": {"head_snapshot_id": "s3"}},
            "snapshots": {
                "s1": {
                    "snapshot_id": "s1",
                    "campaign_id": "c1",
                    "parent_snapshot_id": None,
                    "game_time": {"day": 5, "time": "10:00"},
                },
                "s2": {
                    "snapshot_id": "s2",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s1",
                    "game_time": {"day": 6, "time": "12:00"},
                },
                "s3": {
                    "snapshot_id": "s3",
                    "campaign_id": "c1",
                    "parent_snapshot_id": "s2",
                    "game_time": {"day": 5, "time": "14:00"},
                },
            },
        }
        # campaign_chain отсекает s2, оставляя s1 -> s3
        daily = select_daily_snapshots(index_data, "s3")
        self.assertEqual(daily, [(5, "s3")])

    def test_skills_history_delta_only(self) -> None:
        """Проверка дельта-трекинга уровней навыков и атрибутов.

        - При первом появлении персонажа фиксируются все начальные непустые навыки.
        - Дробные изменения (10.2 -> 10.8) НЕ создают строк.
        - Целочисленные изменения (10.8 -> 11.1) создают ровно одну запись.
        - Снижение уровня (деградация: 15 -> 14) также фиксируется.
        - Персонаж без изменений за день не порождает записей.
        - Новый персонаж на позднем дне записывает все свои начальные навыки.
        """
        snap_day1 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хот Лон",
                    "attributes_base": {"Strength": 10.2, "Toughness": 20.0},
                    "skills_base": {"Katanas": 15.4, "Athletics": 30.0},
                }
            ]
        }
        snap_day2 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хот Лон",
                    # Дробные изменения: 10.2 -> 10.8, 15.4 -> 15.9, Toughness и Athletics без изменений
                    "attributes_base": {"Strength": 10.8, "Toughness": 20.0},
                    "skills_base": {"Katanas": 15.9, "Athletics": 30.0},
                }
            ]
        }
        snap_day3 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хот Лон",
                    # Целочисленный рост: Strength 10.8 -> 11.1 (int: 10 -> 11)
                    "attributes_base": {"Strength": 11.1, "Toughness": 20.0},
                    "skills_base": {"Katanas": 15.9, "Athletics": 30.0},
                }
            ]
        }
        snap_day4 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хот Лон",
                    # Целочисленное снижение: Katanas 15.9 -> 14.8 (int: 15 -> 14)
                    "attributes_base": {"Strength": 11.1, "Toughness": 20.0},
                    "skills_base": {"Katanas": 14.8, "Athletics": 30.0},
                }
            ]
        }
        snap_day5 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хот Лон",
                    # Без изменений
                    "attributes_base": {"Strength": 11.1, "Toughness": 20.0},
                    "skills_base": {"Katanas": 14.8, "Athletics": 30.0},
                },
                {
                    "id": "ps:2",
                    "name": "Кан",
                    # Новый рекрут: все непустые статы фиксируются
                    "attributes_base": {"Strength": 50.0},
                    "skills_base": {"Heavy Weapons": 40.5},
                },
            ]
        }

        daily_snaps = [
            (1, snap_day1),
            (2, snap_day2),
            (3, snap_day3),
            (4, snap_day4),
            (5, snap_day5),
        ]

        table = build_skills_history(daily_snaps)
        self.assertEqual(table.name, "skills_history.csv")
        self.assertEqual(table.sort_key, "day, character_id, skill")
        self.assertEqual(table.join_keys, "character_id")

        rows = table.rows
        # День 1: 4 записи начального состояния для ps:1 (Athletics=30, Katanas=15, Strength=10, Toughness=20)
        day1_rows = [r for r in rows if r["day"] == 1]
        self.assertEqual(len(day1_rows), 4)
        self.assertEqual([(r["skill"], r["level"]) for r in day1_rows], [
            ("Athletics", 30),
            ("Katanas", 15),
            ("Strength", 10),
            ("Toughness", 20),
        ])

        # День 2: 0 записей (только дробные изменения)
        day2_rows = [r for r in rows if r["day"] == 2]
        self.assertEqual(len(day2_rows), 0)

        # День 3: ровно 1 запись (Strength 10 -> 11)
        day3_rows = [r for r in rows if r["day"] == 3]
        self.assertEqual(len(day3_rows), 1)
        self.assertEqual(day3_rows[0]["character_id"], "ps:1")
        self.assertEqual(day3_rows[0]["skill"], "Strength")
        self.assertEqual(day3_rows[0]["level"], 11)

        # День 4: ровно 1 запись (Katanas 15 -> 14)
        day4_rows = [r for r in rows if r["day"] == 4]
        self.assertEqual(len(day4_rows), 1)
        self.assertEqual(day4_rows[0]["character_id"], "ps:1")
        self.assertEqual(day4_rows[0]["skill"], "Katanas")
        self.assertEqual(day4_rows[0]["level"], 14)

        # День 5: ps:1 без изменений (0 строк), ps:2 новый (2 строки)
        day5_rows = [r for r in rows if r["day"] == 5]
        self.assertEqual(len(day5_rows), 2)
        self.assertEqual([(r["character_id"], r["skill"], r["level"]) for r in day5_rows], [
            ("ps:2", "Heavy Weapons", 40),
            ("ps:2", "Strength", 50),
        ])

        # Общее число строк
        self.assertEqual(len(rows), 8)

    def test_campaign_history(self) -> None:
        """Проверка построения campaign_history.csv: казна, время и численность отряда."""
        daily_snaps = [
            (
                1,
                {
                    "world": {"money": 1500, "time": "08:15"},
                    "characters": [{"id": "ps:1"}],
                },
            ),
            (
                2,
                {
                    "world": {"money": 3200, "time": "14:45"},
                    "characters": [{"id": "ps:1"}, {"id": "ps:2"}],
                },
            ),
            (
                3,
                {
                    # world без time -> fallback "00:00"
                    "world": {"money": 10000},
                    "characters": [{"id": "ps:1"}, {"id": "ps:2"}, {"id": "ps:3"}],
                },
            ),
        ]
        table = build_campaign_history(daily_snaps)
        self.assertEqual(table.name, "campaign_history.csv")
        self.assertEqual(table.sort_key, "day")
        self.assertEqual(table.join_keys, "day")
        self.assertEqual(len(table.rows), 3)

        self.assertEqual(table.rows[0], {
            "day": 1,
            "game_time": "Day 1 08:15",
            "cats": 1500,
            "squad_size": 1,
        })
        self.assertEqual(table.rows[1], {
            "day": 2,
            "game_time": "Day 2 14:45",
            "cats": 3200,
            "squad_size": 2,
        })
        self.assertEqual(table.rows[2], {
            "day": 3,
            "game_time": "Day 3 00:00",
            "cats": 10000,
            "squad_size": 3,
        })

    def test_relations_history(self) -> None:
        """Проверка построения relations_history.csv: фиксация начальных значений и изменений."""
        daily_snaps = [
            (
                1,
                {
                    "diplomacy": {
                        "relations": [
                            {"faction_sid": "f-shek", "faction_name": "Шеки", "relation": 0.0},
                            {"faction_sid": "f-hn", "faction_name": "Святая Нация", "relation": -100.0},
                        ]
                    }
                },
            ),
            (
                2,
                {
                    # f-shek и f-hn без изменений, добавилась фракция f-uc
                    "diplomacy": {
                        "relations": [
                            {"faction_sid": "f-shek", "faction_name": "Шеки", "relation": 0.0},
                            {"faction_sid": "f-hn", "faction_name": "Святая Нация", "relation": -100.0},
                            {"faction_sid": "f-uc", "faction_name": "Союзные Города", "relation": 10.0},
                        ]
                    }
                },
            ),
            (
                3,
                {
                    # f-shek улучшились до 25.0, f-hn и f-uc без изменений
                    "diplomacy": {
                        "relations": [
                            {"faction_sid": "f-shek", "faction_name": "Шеки", "relation": 25.0},
                            {"faction_sid": "f-hn", "faction_name": "Святая Нация", "relation": -100.0},
                            {"faction_sid": "f-uc", "faction_name": "Союзные Города", "relation": 10.0},
                        ]
                    }
                },
            ),
        ]
        table = build_relations_history(daily_snaps)
        self.assertEqual(table.name, "relations_history.csv")
        self.assertEqual(table.sort_key, "day, faction_sid")
        self.assertEqual(table.join_keys, "faction_sid")

        # День 1: 2 записи (f-hn, f-shek)
        day1 = [r for r in table.rows if r["day"] == 1]
        self.assertEqual(len(day1), 2)
        self.assertEqual([(r["faction_sid"], r["relation"]) for r in day1], [
            ("f-hn", -100.0),
            ("f-shek", 0.0),
        ])

        # День 2: 1 запись (f-uc)
        day2 = [r for r in table.rows if r["day"] == 2]
        self.assertEqual(len(day2), 1)
        self.assertEqual(day2[0]["faction_sid"], "f-uc")
        self.assertEqual(day2[0]["relation"], 10.0)

        # День 3: 1 запись (f-shek)
        day3 = [r for r in table.rows if r["day"] == 3]
        self.assertEqual(len(day3), 1)
        self.assertEqual(day3[0]["faction_sid"], "f-shek")
        self.assertEqual(day3[0]["relation"], 25.0)

        self.assertEqual(len(table.rows), 4)

    def test_campaign_history_and_relations_history(self) -> None:
        """Комплексная проверка форматов, колонок и пустого ввода для campaign и relations."""
        # Пустой ввод
        empty_camp = build_campaign_history([])
        self.assertEqual(empty_camp.rows, [])
        self.assertEqual([c[0] for c in empty_camp.columns], ["day", "game_time", "cats", "squad_size"])

        empty_rel = build_relations_history([])
        self.assertEqual(empty_rel.rows, [])
        self.assertEqual([c[0] for c in empty_rel.columns], ["day", "faction_sid", "faction_name", "relation"])

        # Проверка CSV-сериализации пустых таблиц
        self.assertEqual(csv_bytes(empty_camp), b"day,game_time,cats,squad_size\n")
        self.assertEqual(csv_bytes(empty_rel), b"day,faction_sid,faction_name,relation\n")

    def test_determinism(self) -> None:
        """Повторный запуск на одинаковых данных даёт побайтно идентичный результат."""
        daily_snaps = [
            (
                1,
                {
                    "world": {"money": 500, "time": "12:00"},
                    "characters": [
                        {"id": "ps:1", "name": "A", "attributes_base": {"Strength": 10.0}, "skills_base": {"Katanas": 5.0}},
                        {"id": "ps:2", "name": "B", "attributes_base": {"Dexterity": 20.0}, "skills_base": {"Dodge": 15.0}},
                    ],
                    "diplomacy": {"relations": [{"faction_sid": "f1", "faction_name": "F1", "relation": 0.0}]},
                },
            ),
            (
                2,
                {
                    "world": {"money": 1200, "time": "18:30"},
                    "characters": [
                        {"id": "ps:1", "name": "A", "attributes_base": {"Strength": 11.2}, "skills_base": {"Katanas": 5.8}},
                        {"id": "ps:2", "name": "B", "attributes_base": {"Dexterity": 20.0}, "skills_base": {"Dodge": 16.0}},
                    ],
                    "diplomacy": {"relations": [
                        {"faction_sid": "f1", "faction_name": "F1", "relation": 10.0},
                        {"faction_sid": "f2", "faction_name": "F2", "relation": -50.0},
                    ]},
                },
            ),
        ]

        t1_skills = csv_bytes(build_skills_history(daily_snaps))
        t2_skills = csv_bytes(build_skills_history(list(reversed(daily_snaps))))
        self.assertEqual(t1_skills, t2_skills)

        t1_camp = csv_bytes(build_campaign_history(daily_snaps))
        t2_camp = csv_bytes(build_campaign_history(list(reversed(daily_snaps))))
        self.assertEqual(t1_camp, t2_camp)

        t1_rel = csv_bytes(build_relations_history(daily_snaps))
        t2_rel = csv_bytes(build_relations_history(list(reversed(daily_snaps))))
        self.assertEqual(t1_rel, t2_rel)

    def test_load_daily_snapshots_and_from_dir(self) -> None:
        """Проверка загрузки снимков через callable и из каталога."""
        data_s1 = {"world": {"day": 1, "time": "10:00"}}
        data_s2 = {"world": {"day": 2, "time": "15:00"}}

        # 1. load_daily_snapshots
        loader_called: list[str] = []

        def dummy_loader(sid: str) -> dict[str, Any]:
            loader_called.append(sid)
            return data_s1 if sid == "s1" else data_s2

        loaded = load_daily_snapshots([(1, "s1"), (2, "s2")], dummy_loader)
        self.assertEqual(loaded, [(1, data_s1), (2, data_s2)])
        self.assertEqual(loader_called, ["s1", "s2"])

        # 2. load_daily_snapshots_from_dir
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "s1").mkdir()
            (tmp_path / "s2").mkdir()
            (tmp_path / "s1" / "current.json").write_bytes(canonical(data_s1))
            (tmp_path / "s2" / "current.json").write_bytes(canonical(data_s2))

            index_data = {
                "format": "kenshi-index/2",
                "campaigns": {"c1": {"head_snapshot_id": "s2"}},
                "snapshots": {
                    "s1": {"snapshot_id": "s1", "campaign_id": "c1", "parent_snapshot_id": None, "game_time": {"day": 1, "time": "10:00"}},
                    "s2": {"snapshot_id": "s2", "campaign_id": "c1", "parent_snapshot_id": "s1", "game_time": {"day": 2, "time": "15:00"}},
                },
            }

            res = load_daily_snapshots_from_dir(index_data, "s2", tmp_path)
            self.assertEqual(res, [(1, data_s1), (2, data_s2)])

    def test_skills_history_negative_skills_filtered(self) -> None:
        """Отрицательные уровни навыков (lvl < 0) отсекаются, а при прокачке (lvl >= 0) корректно фиксируются."""
        snap_day1 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хоббс",
                    "attributes_base": {"Strength": 10.0},
                    "skills_base": {
                        "Armour Smithing": -1.72,
                        "Crossbows": -3.45,
                        "Dodge": -2.85,
                        "Katanas": 5.0,
                    },
                }
            ]
        }
        snap_day2 = {
            "characters": [
                {
                    "id": "ps:1",
                    "name": "Хоббс",
                    # Crossbows прокачан с -3.45 до 5.2 (int: 5), Armour Smithing остался отрицательным (-1.2)
                    "attributes_base": {"Strength": 10.0},
                    "skills_base": {
                        "Armour Smithing": -1.2,
                        "Crossbows": 5.2,
                        "Dodge": -2.85,
                        "Katanas": 5.0,
                    },
                }
            ]
        }
        table = build_skills_history([(1, snap_day1), (2, snap_day2)])
        rows = table.rows

        # 1. Ни одной строки с level < 0 не попадает в таблицу
        self.assertTrue(all(r["level"] >= 0 for r in rows))

        # 2. На день 1 присутствуют только положительные навыки/атрибуты (Katanas=5, Strength=10),
        # а навыки с lvl < 0 (Armour Smithing=-1, Crossbows=-3, Dodge=-2) отфильтрованы
        day1_rows = [r for r in rows if r["day"] == 1]
        self.assertEqual([(r["skill"], r["level"]) for r in day1_rows], [
            ("Katanas", 5),
            ("Strength", 10),
        ])

        # 3. На день 2 зафиксирована прокачка ранее отрицательного навыка (Crossbows 5.2 -> 5)
        day2_rows = [r for r in rows if r["day"] == 2]
        self.assertEqual(len(day2_rows), 1)
        self.assertEqual(day2_rows[0]["character_id"], "ps:1")
        self.assertEqual(day2_rows[0]["skill"], "Crossbows")
        self.assertEqual(day2_rows[0]["level"], 5)

        # 4. Проверка на реальном снимке кампании из data/index.json (head hot-lon)
        repo_root = Path(__file__).resolve().parent.parent
        index_path = repo_root / "data" / "index.json"
        snapshots_dir = repo_root / "data" / "snapshots"
        if not index_path.is_file() or not snapshots_dir.is_dir():
            self.skipTest(f"Каталог данных кампании отсутствует: {index_path}")

        index_data = read_json(index_path.read_bytes())
        head_sid = index_data["campaigns"]["hot-lon"]["head_snapshot_id"]
        daily_snaps = load_daily_snapshots_from_dir(index_data, head_sid, snapshots_dir)
        real_table = build_skills_history(daily_snaps)

        real_negative = [r for r in real_table.rows if r["level"] < 0]
        self.assertEqual(real_negative, [])
        self.assertGreater(len(real_table.rows), 0)


if __name__ == "__main__":
    unittest.main()
