"""Тесты витрины гарнизонов объектов location_garrisons.csv."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import unittest

from tools.location_garrisons import (
    LOCATION_GARRISONS_COLUMNS,
    build_location_garrisons,
    location_garrisons_table,
)


def _make_unit(
    sid: str,
    name: str = "",
    kind: str = "character",
    stat_attack: str = "",
    stat_defence: str = "",
    stat_strength: str = "",
    strength: str = "",
    stat_toughness2: str = "",
    combat_stats: str = "",
    animal_strength: str = "",
    stats_randomise: str = "",
    armour_grade: str = "",
) -> dict[str, str]:
    return {
        "sid": sid,
        "kind": kind,
        "name_en": name or sid,
        "name_ru": name or sid,
        "stat_attack": stat_attack,
        "stat_defence": stat_defence,
        "stat_strength": stat_strength,
        "strength": strength,
        "stat_toughness2": stat_toughness2,
        "combat_stats": combat_stats,
        "animal_strength": animal_strength,
        "stats_randomise": stats_randomise,
        "armour_grade": armour_grade,
    }


def _make_member(
    squad_sid: str,
    role: str,
    name: str,
    v0: str,
    v1: str = "0",
    sid: str | None = None,
) -> dict[str, str]:
    m_sid = sid or name
    return {
        "squad_sid": squad_sid,
        "squad_name": squad_sid,
        "role": role,
        "member_sid": m_sid,
        "member_name": name,
        "member_type": "CHARACTER",
        "v0": v0,
        "v1": v1,
        "v2": "0",
    }


def _make_template(
    sid: str,
    name: str = "",
    faction: str = "",
    faction_sids: str = "",
    states: str = "",
    chars: tuple[str, str] = ("0", "0"),
) -> dict[str, str]:
    return {
        "sid": sid,
        "name_en": name or sid,
        "name_ru": name or sid,
        "faction_sids": faction_sids,
        "faction_names": faction,
        "world_state_sids": states,
        "num_random_chars": chars[0],
        "num_random_chars_max": chars[1],
    }


def _make_gear(
    template_sid: str,
    category: str,
    ref_name: str,
    ref_type: str = "WEAPON",
) -> dict[str, str]:
    return {
        "template_sid": template_sid,
        "template_name": template_sid,
        "category": category,
        "ref_sid": ref_name,
        "ref_name": ref_name,
        "ref_type": ref_type,
        "v0": "1",
        "v1": "100",
        "v2": "0",
    }


class TestLocationGarrisonsSynthetic(unittest.TestCase):
    """Синтетические тесты построения витрины гарнизонов."""

    def test_empty_or_missing_inputs(self) -> None:
        self.assertEqual(build_location_garrisons(None, None), [])
        self.assertEqual(build_location_garrisons({}, {}), [])
        self.assertEqual(build_location_garrisons({"locations": []}, {"units": {}}), [])

    def test_skips_locations_without_residents(self) -> None:
        world_ref = {
            "locations": [
                {"instance_id": "loc-1", "template_sid": "town-empty", "name_ru": "Руины"},
                {"instance_id": "loc-2", "template_sid": "town-missing", "name_ru": "Неизвестно"},
            ]
        }
        records = {
            "units": {
                "towns": [
                    {"sid": "town-empty", "name": "Empty", "refs": {}},
                ]
            }
        }
        rows = build_location_garrisons(world_ref, records)
        self.assertEqual(rows, [])

    def test_location_with_garrison_and_enc_tables(self) -> None:
        world_ref = {
            "locations": [
                {
                    "instance_id": "loc-tower",
                    "template_sid": "town-tower",
                    "name_en": "Abuse Tower",
                    "name_ru": "Башня Зла",
                    "kind": "ruins",
                    "region": "The Deadlands",
                }
            ]
        }
        records = {
            "units": {
                "towns": [
                    {
                        "sid": "town-tower",
                        "name": "Abuse Tower",
                        "refs": {
                            "faction": [["fac-bad", [0, 0, 0]]],
                            "residents": [["sq-bosses", [1, 2, 0]]],
                        },
                    }
                ],
                "biomes": [
                    {"sid": "bio-1", "name": "The Deadlands"},
                ],
            }
        }
        game_index = {
            "bio-1": ["The Deadlands", "Мертвые земли", "BIOME_GROUP", "base.mod", None],
            "fac-bad": ["Bad Guys", "Плохие парни", "FACTION", "base.mod", None],
        }
        enc_tables = {
            "squad_templates": [
                _make_template("sq-bosses", name="Банда Босса", states="ws_deadlands"),
            ],
            "squad_members": [
                _make_member("sq-bosses", "leader", "Главарь", "1", sid="u_ldr"),
                _make_member("sq-bosses", "squad", "Разбойни/кца1/", "2", "4", sid="u_reg1"),
                _make_member("sq-bosses", "squad", "Снайпер", "1", "2", sid="u_reg2"),
            ],
            "unit_templates": [
                _make_unit("u_ldr", "Главарь", stat_attack="55", stat_defence="50", stat_strength="60",
                           stat_toughness2="52", stats_randomise="5", armour_grade="4"),
                _make_unit("u_reg1", "Разбойни/кца1/", stat_attack="20", stat_defence="22", stat_strength="25",
                           stat_toughness2="20", stats_randomise="3", armour_grade="2"),
                _make_unit("u_reg2", "Снайпер", stat_attack="40", stat_defence="35", stat_strength="30",
                           stat_toughness2="32", stats_randomise="4", armour_grade="3"),
            ],
            "unit_template_gear": [
                _make_gear("u_ldr", "weapons", "Дзуттэ"),
                _make_gear("u_ldr", "weapon level", "Катун"),
                _make_gear("u_ldr", "clothing", "Пыльник"),
                _make_gear("u_reg1", "weapons", "Катана"),
                _make_gear("u_reg2", "weapons", "Арбалет"),
            ],
            "faction_hostility": [
                {"faction_sid": "fac-bad", "faction_name_ru": "Плохие парни", "faction_name_en": "Bad Guys"},
            ],
        }

        table = location_garrisons_table(world_ref, records, game_index, enc_tables=enc_tables)
        self.assertEqual(table.name, "location_garrisons.csv")
        self.assertEqual(table.sort_key, "instance_id, squad_name, squad_sid")
        self.assertEqual(table.join_keys, "instance_id, template_sid, squad_sid, faction_sid")
        self.assertEqual(len(table.rows), 1)

        row = table.rows[0]
        self.assertEqual(row["instance_id"], "loc-tower")
        self.assertEqual(row["location_name_en"], "Abuse Tower")
        self.assertEqual(row["location_name_ru"], "Башня Зла")
        self.assertEqual(row["template_sid"], "town-tower")
        self.assertEqual(row["kind"], "ruins")
        self.assertEqual(row["region_name_en"], "The Deadlands")
        self.assertEqual(row["region_name_ru"], "Мертвые земли")
        self.assertEqual(row["squad_sid"], "sq-bosses")
        self.assertEqual(row["squad_name"], "Банда Босса")
        self.assertEqual(row["faction_sid"], "fac-bad")
        self.assertEqual(row["faction_name"], "Плохие парни")
        self.assertEqual(row["spawn_share"], "100%")
        self.assertEqual(row["squad_size_min"], 4)  # 1 + 2 + 1
        self.assertEqual(row["squad_size_max"], 7)  # 1 + 4 + 2
        self.assertIn("Главарь ×1", row["members_summary"])
        self.assertIn("Разбойник 2–4", row["members_summary"])
        self.assertIn("Снайпер 1–2", row["members_summary"])

        # Лидер
        self.assertEqual(row["leader_template_name"], "Главарь")
        self.assertEqual(row["leader_attack"], 55)
        self.assertEqual(row["leader_defence"], 50)
        self.assertEqual(row["leader_strength"], 60)
        self.assertEqual(row["leader_toughness"], 52)
        self.assertEqual(row["leader_stats_randomise"], 5)
        self.assertEqual(row["leader_weapon"], "Дзуттэ (Катун)")
        self.assertEqual(row["leader_armour"], "Пыльник (Специалист)")

        # Рядовые
        self.assertEqual(row["members_attack_min"], 20)
        self.assertEqual(row["members_attack_median"], 30)  # median of 20 and 40
        self.assertEqual(row["members_attack_max"], 40)
        self.assertEqual(row["members_defence_min"], 22)
        self.assertEqual(row["members_defence_median"], 28)
        self.assertEqual(row["members_defence_max"], 35)
        self.assertEqual(row["members_stats_randomise"], "3–4")
        self.assertEqual(row["weapons_typical"], "Арбалет; Катана")
        self.assertEqual(row["armour_grade_max"], "4 (Специалист)")
        self.assertEqual(row["world_state_conditions"], "ws_deadlands")

    def test_leader_only_squad_inherits_stats(self) -> None:
        world_ref = {
            "locations": [
                {"instance_id": "loc-solo", "template_sid": "town-solo", "name_en": "Solo"}
            ]
        }
        records = {
            "units": {
                "towns": [
                    {"sid": "town-solo", "name": "Solo", "refs": {"residents": [["sq-solo", [1, 0, 0]]]}}
                ]
            }
        }
        enc_tables = {
            "squad_templates": [_make_template("sq-solo", name="Одиночка")],
            "squad_members": [_make_member("sq-solo", "leader", "Одинокий Волк", "1", sid="u_wolf")],
            "unit_templates": [_make_unit("u_wolf", "Одинокий Волк", stat_attack="70", stat_defence="65",
                                          stat_strength="80", stat_toughness2="75", stats_randomise="2")],
            "unit_template_gear": [],
            "faction_hostility": [],
        }
        rows = build_location_garrisons(world_ref, records, enc_tables=enc_tables)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["leader_template_name"], "Одинокий Волк")
        self.assertEqual(r["leader_attack"], 70)
        # Рядовых нет -> наследуются от лидера
        self.assertEqual(r["members_attack_min"], 70)
        self.assertEqual(r["members_attack_median"], 70)
        self.assertEqual(r["members_attack_max"], 70)
        self.assertEqual(r["members_stats_randomise"], "2")


class TestLocationGarrisonsRealData(unittest.TestCase):
    """Тест на реальных данных проекта (Башня Жестокости и Древняя лаборатория)."""

    def setUp(self) -> None:
        self.wref_path = Path("published/world_reference.json")
        self.snap_path = Path("published/extended/current.json")
        self.cache_dir = Path("data/cache")
        if not self.wref_path.is_file() or not self.snap_path.is_file() or not self.cache_dir.is_dir():
            self.skipTest("Реальные данные или кэш gamedb отсутствуют")

        with self.wref_path.open(encoding="utf-8") as f:
            self.wref = json.load(f)
        with self.snap_path.open(encoding="utf-8") as f:
            self.snap = json.load(f)

        from tools.tables import load_game_data
        self.idx, self.rec, notes = load_game_data(self.snap, self.cache_dir)
        if not self.rec or not self.idx:
            self.skipTest(f"Справочник игры не загружен: {notes}")

    def test_tower_of_abuse_and_ancient_lab(self) -> None:
        rows = build_location_garrisons(self.wref, self.rec, self.idx)
        self.assertGreater(len(rows), 0)

        by_instance = {r["instance_id"]: r for r in rows}

        # 1. Башня Жестокости (108-rebirth-INGAME)
        TOWER_ID = "108-rebirth-INGAME"
        self.assertIn(TOWER_ID, by_instance)
        tower = by_instance[TOWER_ID]

        self.assertEqual(tower["location_name_en"], "Tower of Abuse")
        self.assertEqual(tower["location_name_ru"], "Башня Жестокости")
        self.assertEqual(tower["region_name_en"], "Venge")
        self.assertEqual(tower["region_name_ru"], "Мщение")
        self.assertEqual(tower["faction_name"], "Хозяева невольников")
        self.assertEqual(tower["spawn_share"], "100%")

        # Лидер: Screamer the False / Ложный крикун
        self.assertIn("Ложный крикун", tower["leader_template_name"])
        self.assertEqual(tower["leader_attack"], 50)
        self.assertEqual(tower["leader_defence"], 50)

        # Состав: Screamer the False, Ponk, Weak Thrall, Thrall
        summary = tower["members_summary"]
        self.assertIn("Ложный крикун", summary)
        self.assertIn("Пон", summary)
        self.assertIn("невольник", summary.lower())

        # Рядовые: мин. 15 (Слабый невольник), макс. 50 (Пон)
        self.assertEqual(tower["members_attack_min"], 15)
        self.assertEqual(tower["members_attack_median"], 25)
        self.assertEqual(tower["members_attack_max"], 50)

        # 2. Древняя лаборатория (132-rebirth-INGAME или 15-rebirth-INGAME)
        lab_id = "132-rebirth-INGAME" if "132-rebirth-INGAME" in by_instance else "15-rebirth-INGAME"
        self.assertIn(lab_id, by_instance)
        lab = by_instance[lab_id]

        self.assertEqual(lab["location_name_en"], "Ancient Labs")
        self.assertEqual(lab["location_name_ru"], "Древние лабы")
        self.assertEqual(lab["faction_name"], "Древние руины")
        self.assertEqual(lab["spawn_share"], "100%")

        # Охранные пауки без лидера
        self.assertEqual(lab["leader_template_name"], "")
        self.assertIn("Охранный паук", lab["members_summary"])
        self.assertEqual(lab["squad_size_min"], 2)
        self.assertEqual(lab["squad_size_max"], 8)
        self.assertEqual(lab["members_attack_min"], 60)
        self.assertEqual(lab["members_attack_max"], 60)


if __name__ == "__main__":
    unittest.main()
