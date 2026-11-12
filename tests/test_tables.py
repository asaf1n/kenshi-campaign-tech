"""Табличные выгрузки published/tables/: схема колонок, порядок строк, валидация манифеста, публикация."""
from __future__ import annotations

import copy
import csv
import io
import math
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.test_publish import _create_sample_snapshot
from tools import itemstats
from tools.publisher import publish
from tools.tables import build_tables, unique_entities_table
from tools.validation import PublicationValidationError, validate_publication, validate_tables

SNAPSHOT_ID = "sha256-mini"

# Эталон колонок небольших таблиц: изменение схемы - осознанное, с обновлением словаря index.md и контракта.
GOLDEN_HEADERS: dict[str, list[str]] = {
    "unique_npcs.csv": ["sid", "name_en", "name_ru", "state_code", "player", "captured_by"],
    "unique_entities.csv": [
        "entity_kind", "sid", "name_ru", "name_en", "state_code", "squad_status", "character_id",
        "primary_location_instance_id", "location_resolution", "meitou_base_sid", "carrier_or_storage",
        "provenance_status",
    ],
    "diplomacy.csv": ["faction_sid", "faction_name", "relation", "trust", "trust_neg"],
    "town_overrides.csv": ["replacement_sid", "town", "raw_name", "variant", "replacement_name", "replacement_name_ru",
                           "faction_sid", "faction_name", "factions_here"],
    "world_reaction_links.csv": ["override_sid", "town_sid", "world_state_sid", "condition", "npc_sid", "npc_name",
                                 "required_state_code", "npc_in_save", "current_state_code", "current_player",
                                 "current_captured_by"],
    "item_prices.csv": ["sid", "name_en", "name_ru", "type", "value", "value_1", "value_source", "value_history"],
}

# Единственная локация справочника мира в клетке 14.2 разрешает здание без поселения; клетка 18.34 остаётся неразрешённой.
WORLD_REF = {"locations": [{"zone": "14.2", "name_ru": "Библиотека"}]}

INDEX = {
    "1965-gamedata.base": ["Hemp", "Пенька", "ITEM", "gamedata.base",
                           {"value": 100, "value_source": "rebirth.mod",
                            "value_history": [["gamedata.base", 50], ["rebirth.mod", 100]]}],
    "476-gamedata.base": ["Katana", "Катана", "WEAPON", "gamedata.base",
                          {"cut damage multiplier": 1.2, "weight kg": 4.0, "value": 900}],
    "1057-gamedata.base": ["Catun", "Катунский Кулибин", "WEAPON_MANUFACTURER", "gamedata.base",
                           {"cut damage mod": 1.2, "blunt damage mod": 1.0, "weight mod": 1.0}],
    "1060-gamedata.base": ["No.3", "Катун No.3", "MATERIAL_SPECS_WEAPON", "gamedata.base",
                           {"attack mod": 2, "defence mod": 0}],
    "npc-a": ["Phoenix", "Феникс", "SQUAD_TEMPLATE", "x.mod", None],
    "npc-b": ["Queen", "Королева", "SQUAD_TEMPLATE", "x.mod", None],
    "town-1": ["Southern Hive", "Южный улей", "TOWN", "x.mod", None],
}

RECORDS = {
    "reactions": {
        "world_states": [
            {"sid": "ws-1", "name": "phoenix free", "refs": {"NPC is": [["npc-a", [1, 0, 0]]]}},
            {"sid": "ws-2", "name": "queen dead", "refs": {"NPC is NOT": [["npc-b", [0, 0, 0]]], "player ally": [["f-1", [1, 0, 0]]]}},
        ],
        # Вложенный override: город достижим только через родительский override.
        "town_overrides": [
            {"sid": "ov-1", "name": "Hive (override) A", "refs": {"override town": [["ov-2", [0, 0, 0]]],
                                                                   "world state": [["ws-1", [1, 0, 0]], ["ws-2", [0, 0, 0]]],
                                                                   "faction": [["f-1", [0, 0, 0]]]}},
            {"sid": "ov-2", "name": "Hive (override) B", "refs": {"world state": [["ws-1", [0, 0, 0]]]}},
        ],
        "town_parents": [{"sid": "town-1", "name": "Southern Hive", "refs": {"override town": [["ov-1", [0, 0, 0]]]}}],
    },
    "trade": {
        "cultures": [{"sid": "c-1", "type": "ITEMS_CULTURE", "name": "shek", "files": ["g.base"],
                      "refs": {"trade prices": [["1230-gamedata.base", [350, 0, 0]]]}}],
        "culture_links": [], "manufacturers": [{"sid": "1057-gamedata.base", "type": "WEAPON_MANUFACTURER",
                                                "name": "Catun", "files": ["g.base"], "price mod": 1.4}],
        "squad_templates": [], "constants": [],
    },
}


def mini_snapshot() -> dict[str, Any]:
    chars = [
        {"id": "ps:2", "name": "Кан", "race": "Шек", "squad_id": "s1", "attributes_base": {"Strength": 50.0},
         "skills_base": {"Katanas": 41.45, "Athletics": 70.26}, "status": {"dead": False},
         "condition": {"blood": 100.0, "hunger_raw": 2.8745}, "weapons": [], "armour": {}, "prosthetics": [],
         "jobs": {"enabled": True, "primary": [{"task_name": "BODYGUARD", "target_name": "Хот Лон"}]}},
        {"id": "ps:1", "name": "Зелёный палец", "race": "Шек", "squad_id": "s1",
         "attributes_base": {"Strength": 30.0}, "skills_base": {"Katanas": 10.0, "Stealth": 5.0},
         "status": {"coma": True}, "condition": {"blood": 90.0}, "combat_modes": {"chase": True},
         "weapons": [{"slot": "weapon_back", "name": "Катана", "sid": "476-gamedata.base", "type": "melee",
                      "grade": "Катун No.3", "manufacturer": "Катунский Кулибин", "level": 40}],
         "armour": {}, "prosthetics": [{"limb": "left_leg", "name": "Нога разведчика", "sid": "p-1",
                                        "level": 95, "grade": "Мастерский"}],
         "items": [{"section": "back", "container": "inventory", "base_data_sid": "476-gamedata.base",
                    "company_sid": "1057-gamedata.base", "material_sid": "1060-gamedata.base", "quality": 100.0}]},
    ]
    buildings = [
        {"id": "b1", "zone": "24.40", "name": "Хранение", "town": "Деревня",
         "storage_items": [{"base_data_sid": "1965-gamedata.base", "quantity": 11, "container": "inventory>c1"}]},
        {"id": "b3", "zone": "14.2", "name": "В клетке с руинами",
         "storage_items": [{"base_data_sid": "1965-gamedata.base", "quantity": 2, "container": "inventory>c3"}]},
        {"id": "b2", "zone": "18.34", "name": "Без города",
         "storage_items": [{"base_data_sid": "1965-gamedata.base", "quantity": 5, "container": "inventory>c2"}]},
    ]
    return {
        "world": {"day": 133, "time": "06:03"}, "characters": chars, "squads": [{"squad_id": "s1", "squad_name": "Primaris"}],
        "property": {"buildings": buildings},
        "diplomacy": {"relations": [{"faction_sid": "f-2", "faction_name": "Болотники", "relation": -0.19, "trust": 0.0, "trust_neg": 0.0},
                                    {"faction_sid": "f-1", "faction_name": "Шеки", "relation": 100.0, "trust": 0.0, "trust_neg": 0.0}]},
        "world_state": {"unique_npcs": [{"sid": "npc-b", "name": "Queen", "name_ru": "Королева", "state_code": 0, "player": False},
                                        {"sid": "npc-a", "name": "Phoenix", "name_ru": "Феникс", "state_code": 2, "player": True}],
                        "town_overrides": []},
        "meta": {"game_data": {"mods": ["base"]}},
    }


def parse(files: dict[str, bytes], name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8"), newline="")))


class TableContentTests(unittest.TestCase):
    def test_town_overrides_clean_prefixes_and_keep_localization(self) -> None:
        """Ловит локализацию города, сырое raw_name и извлечение variant."""
        cases = [
            ("The Hub", "Town state The Hub", "Хаб", "Town state The Hub"),
            ("Shark", "Shark", "Акула", "Shark"),
            ("Town state Shark", "Town state Shark", "Акула", "Town state Shark"),
            (None, None, "", ""),
            ("", "", "", ""),
        ]
        for name, raw_name, expected_name, expected_raw in cases:
            with self.subTest(name=name, raw_name=raw_name):
                cur = mini_snapshot()
                town = {"name": name, "raw_name": raw_name, "replacement_sid": "override-sid",
                        "replacement_name": "Shark (override) all ded", "replacement_name_ru": None}
                cur["world_state"]["town_overrides"] = [town]
                files = build_tables(cur, None, SNAPSHOT_ID, INDEX, RECORDS, world_ref=WORLD_REF)
                row = parse(files, "town_overrides.csv")[0]
                self.assertEqual((row["town"], row["raw_name"]), (expected_name, expected_raw))
                self.assertEqual(row["variant"], "all ded")
                self.assertEqual(row["replacement_name"], "Shark (override) all ded")
                self.assertEqual(row["replacement_sid"], "override-sid")
                self.assertEqual((town["name"], town["raw_name"]), (name, raw_name))

    def test_schema_order_and_values(self) -> None:
        """Ловит дрейф колонок, недетерминизм порядка строк (вход перемешан) и потерю сырых значений."""
        cur = mini_snapshot()
        files = build_tables(cur, None, SNAPSHOT_ID, INDEX, RECORDS, world_ref=WORLD_REF)
        shuffled = copy.deepcopy(cur)
        random.Random(7).shuffle(shuffled["characters"])
        shuffled["property"]["buildings"].reverse()
        shuffled["world_state"]["unique_npcs"].reverse()
        shuffled["diplomacy"]["relations"].reverse()
        self.assertEqual(files, build_tables(shuffled, None, SNAPSHOT_ID, INDEX, RECORDS, world_ref=WORLD_REF))

        for name, header in GOLDEN_HEADERS.items():
            with self.subTest(name=name):
                self.assertEqual(files[name].decode("utf-8").split("\n", 1)[0].split(","), header)
        self.assertFalse(files["squad.csv"].startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", files["squad.csv"])

        squad = parse(files, "squad.csv")
        self.assertEqual([r["character_id"] for r in squad], ["ps:1", "ps:2"])
        self.assertEqual([k for k in squad[0] if k in ("Athletics", "Katanas", "Stealth", "Strength")],
                         ["Strength", "Athletics", "Katanas", "Stealth"])
        self.assertEqual(squad[1]["jobs_primary"], "BODYGUARD [Хот Лон]")
        self.assertEqual(parse(files, "diplomacy.csv")[1]["relation"], "-0.19")
        self.assertEqual(parse(files, "unique_npcs.csv")[0]["state_code"], "2")

        weapon = [r for r in parse(files, "equipment.csv") if r["kind"] == "weapon"][0]
        self.assertEqual((weapon["grade"], weapon["manufacturer_cut_damage_mod"], weapon["grade_attack_mod"],
                          weapon["tpl_cut_damage_multiplier"], weapon["quality"]), ("Катун No.3", "1.2", "2", "1.2", "100"))
        prices = {r["sid"]: r for r in parse(files, "item_prices.csv")}
        self.assertEqual((prices["1965-gamedata.base"]["value"], prices["1965-gamedata.base"]["value_history"]),
                         ("100", "gamedata.base=50; rebirth.mod=100"))
        storage = parse(files, "storage.csv")
        self.assertEqual([(r["location_key"], r["item"], r["base_price"]) for r in storage],
                         [("Библиотека", "Пенька", "100"), ("Деревня", "Пенька", "100"),
                          ("зона 18.34 (поселение не определено)", "Пенька", "100")])
        self.assertEqual({r["location_key"]: r["settlement_status"] for r in storage}["Библиотека"], "zone_location")

        links = parse(files, "world_reaction_links.csv")
        reactions = parse(files, "world_reactions.csv")
        self.assertEqual({r["town_sid"] for r in reactions}, {"town-1"})  # вложенный ov-2 разворачивается до города
        self.assertEqual(len(reactions), 4)
        self.assertEqual([(r["npc_sid"], r["required_state_code"], r["current_state_code"]) for r in links
                          if r["override_sid"] == "ov-1"], [("npc-a", "1", "2"), ("npc-b", "0", "0")])

    def test_without_game_data_is_reported(self) -> None:
        """Без справочника игры пустые таблицы не выдаются за полные: причина названа в index.md."""
        files = build_tables(mini_snapshot(), None, SNAPSHOT_ID, None, None, ["причина-1"])
        self.assertEqual(parse(files, "item_prices.csv"), [])
        text = files["index.md"].decode("utf-8")
        self.assertIn("причина-1", text)
        self.assertIn("Справочник игры недоступен", text)


# Эталон calc_* по карточкам игры (воронёная кольчуга level 80, катана level 70, нога разведчика level 95);
# шаблоны - реальные записи справочника. Карточка усекает значения: 42.48 -> 42, 10.13 -> 10 кг.
CALC_INDEX = {
    "chain": ["Blackened Chainmail", "Воронёная кольчуга", "ARMOUR", "gamedata.base",
              {"class": 2, "material type": 2, "slot": 8, "cut into stun": 0.4, "level bonus": 10, "blunt def bonus": 0.0,
               "cut def bonus": 0.0, "pierce def mult": 1.0, "weight kg": 13.0, "stealth mult": 1.2,
               "ranged skill mult": 0.7, "dodge mult": 0.9, "dexterity mult": 0.85, "combat speed mult": 0.97,
               "refs:part coverage": {"p1": [100, 100, 0], "p2": [100, 100, 0], "p3": [100, 100, 0], "chest": [100, 100, 0]}}],
    "chest": ["Chest", "Грудь", "LOCATIONAL_DAMAGE", "gamedata.base", None],
    "katana": ["Guardless Katana", "Катана без гарды", "WEAPON", "gamedata.base",
               {"cut damage multiplier": 1.0, "blunt damage multiplier": 0.0, "weight kg": 1.0, "weight mult": 1.0, "skill category": 0}],
    "edge": ["Edgewalkers", "Идущие по Клинку", "WEAPON_MANUFACTURER", "gamedata.base",
             {"cut damage mod": 1.1, "blunt damage mod": 1.0, "weight mod": 1.0}],
    "leg": ["Scout Leg (left)", "Нога разведчика (левая)", "LIMB_REPLACEMENT", "Newwworld.mod",
            {"slot": 52, "weight kg": 10.0, "HP": 50, "HP 1": 150, "athletics mult": 1.0, "athletics mult 1": 1.4,
             "swimming mult": 0.75, "swimming mult 1": 1.0, "stealth mult": 0.25, "stealth mult 1": 0.95}],
}
CALC_EXPECTED: dict[str, dict[str, str]] = {
    "armour": {"calc_status": "verified", "calc_level": "80", "calc_cut_resist_pct": "42.48", "calc_blunt_resist_pct": "19.44",
               "calc_harpoon_resist": "27.000001", "calc_cut_efficiency": "0.6", "calc_weight_kg": "10.13",
               "calc_stealth_mult": "0.7128", "calc_ranged_mult": "0.7", "calc_dodge_mult": "0.9",
               "calc_dexterity_mult": "0.85", "calc_combat_speed_mult": "0.97", "calc_athletics_mult": "1",
               "calc_cut_damage": "", "calc_hp_max": ""},
    "weapon": {"calc_status": "verified", "calc_level": "70", "calc_cut_damage": "1.639", "calc_blunt_damage": "0", "calc_weight_kg": "1",
               "calc_cut_resist_pct": ""},
    "prosthetic": {"calc_status": "verified", "calc_level": "95", "calc_hp_max": "145", "calc_stealth_mult": "0.915",
                   "calc_athletics_mult": "1.38", "calc_swimming_mult": "0.9875", "calc_weight_kg": ""},
}


UNIT_INDEX = {
    "c-1": ["Raider", "Рейдер", "CHARACTER", "x.mod", None],
    "c-2": ["Pup", "Щенок", "ANIMAL_CHARACTER", "x.mod", None],
    "r-1": ["Greenlander", "Зеленоземец", "RACE", "x.mod", None],
    "w-1": ["Katana", "Катана", "WEAPON", "x.mod", None],
    "sq-1": ["Patrol", "Патруль", "SQUAD_TEMPLATE", "x.mod", None],
    "f-1": ["Nation", "Нация", "FACTION", "x.mod", None],
    "f-2": ["Rats", "Крысы", "FACTION", "x.mod", None],
}
UNIT_RECORDS = {"units": {
    "characters": [
        {"sid": "c-2", "type": "ANIMAL_CHARACTER", "name": "Pup", "files": ["x.mod"], "int": {"female chance": 50}},
        {"sid": "c-1", "type": "CHARACTER", "name": "Raider", "files": ["x.mod"],
         "int": {"combat stats": 40, "armour grade": 2}, "bool": {"unique": False},
         "refs": {"race": [["r-1", [100, 0, 0]]], "stats": [["st-1", [0, 0, 0]]],
                  "weapons": [["w-1", [1, 1, 100]]], "clothing": [["unknown", [0, 100, 0]]]}},
    ],
    "stats": [{"sid": "st-1", "type": "STATS", "float": {"attack": 30.0, "katana": 55.5, "unlisted": 9.0}}],
    "squads": [{"sid": "sq-1", "type": "SQUAD_TEMPLATE", "name": "Patrol", "files": ["x.mod"],
                "refs": {"leader": [["c-1", [1, 0, 0]]], "animals": [["c-2", [1, 2, 100]]],
                         "world state": [["ws-1", [0, 0, 0]]]}}],
    "biomes": [{"sid": "b-1", "type": "BIOME_GROUP", "name": "Desert", "homeless spawn amount": 1.5,
                "refs": {"homeless spawns": [["sq-1", [15, 0, 0]]]}}],
    "towns": [{"sid": "t-1", "type": "TOWN", "name": "Hub", "refs": {"roaming squads": [["sq-1", [0, 0, 0]]],
                                                                       "faction": [["f-1", [0, 0, 0]]]}}],
    "factions": [{"sid": "f-1", "type": "FACTION", "name": "Nation", "files": ["x.mod"],
                  "int": {"default relation": 0, "enemy classification": -10},
                  "refs": {"relations": [["f-2", [-100, 0, 0]]], "coexistence": [["f-2", [0, 0, 0]]],
                           "races": [["r-1", [100, 0, 0]]]}}],
}}


class UnitReferenceTests(unittest.TestCase):
    def test_unit_reference_tables(self) -> None:
        """Ловит: STATS чужой записи или незаданное поле вместо пустоты, значение relation у coexistence,
        смешение источников появления, потерю sid при неизвестной ссылке, падение без справочника игры."""
        files = build_tables(mini_snapshot(), None, SNAPSHOT_ID, UNIT_INDEX, UNIT_RECORDS)
        units = parse(files, "unit_templates.csv")
        self.assertEqual([(r["sid"], r["kind"]) for r in units], [("c-1", "character"), ("c-2", "animal")])
        raider = units[0]
        self.assertEqual((raider["race_sids"], raider["race_names"], raider["race_weights"], raider["stats_sid"]),
                         ("r-1", "Зеленоземец", "100", "st-1"))
        self.assertEqual((raider["stat_attack"], raider["stat_katana"], raider["stat_dodge"], raider["combat_stats"]),
                         ("30", "55.5", "", "40"))
        self.assertNotIn("stat_unlisted", raider)
        self.assertEqual((units[1]["stats_sid"], units[1]["stat_attack"]), ("", ""))

        gear = parse(files, "unit_template_gear.csv")
        self.assertEqual([(r["category"], r["ref_sid"], r["ref_name"], r["v0"], r["v1"], r["v2"]) for r in gear],
                         [("clothing", "unknown", "", "0", "100", "0"), ("weapons", "w-1", "Катана", "1", "1", "100")])

        members = parse(files, "squad_members.csv")
        self.assertEqual([(r["role"], r["member_sid"], r["member_type"], r["v2"]) for r in members],
                         [("animals", "c-2", "ANIMAL_CHARACTER", "100"), ("leader", "c-1", "CHARACTER", "0")])
        squad = parse(files, "squad_templates.csv")[0]
        self.assertEqual((squad["world_state_sids"], squad["world_state_required"]), ("ws-1", "0"))

        spawns = parse(files, "squad_spawns.csv")
        self.assertEqual([(r["source"], r["place_sid"], r["spawn_amount"], r["place_faction_sid"], r["ref_v0"]) for r in spawns],
                         [("biome_homeless", "b-1", "1.5", "", "15"), ("town_roaming", "t-1", "", "f-1", "0")])

        relations = parse(files, "faction_relations.csv")
        self.assertEqual([(r["kind"], r["other_sid"], r["value"]) for r in relations],
                         [("coexistence", "f-2", ""), ("relation", "f-2", "-100")])
        hostility = parse(files, "faction_hostility.csv")[0]
        self.assertEqual((hostility["default_relation"], hostility["enemy_classification"], hostility["race_names"]),
                         ("0", "-10", "Зеленоземец"))

        empty = build_tables(mini_snapshot(), None, SNAPSHOT_ID)
        for name in ("unit_templates.csv", "unit_template_gear.csv", "squad_templates.csv", "squad_members.csv",
                     "squad_spawns.csv", "faction_relations.csv", "faction_hostility.csv",
                     "research.csv", "build_catalog.csv", "biome_resources.csv"):
            self.assertEqual(len(empty[name].decode("utf-8").splitlines()), 1, name)

    def test_production_and_research_tables(self) -> None:
        """Ловит перепутанный порядок слотов стройматериалов (construction_order vs сортировка по sid),
        неверный статус исследования (locked_prereq/available/completed), потерю unlock_status и привязки работников."""
        prod_idx = {
            **INDEX,
            "580-gamedata.base": ["Building Material", "Стройматериалы", "ITEM", "gamedata.base", None],
            "m-iron": ["Iron Plates", "Железные листы", "ITEM", "gamedata.base", None],
            "m-elec": ["Electrical", "Электродетали", "ITEM", "gamedata.base", None],
            "res-1": ["Tech 1", "Тех 1", "RESEARCH", "gamedata.base", None],
            "res-2": ["Tech 2", "Тех 2", "RESEARCH", "gamedata.base", None],
            "res-3": ["Tech 3", "Тех 3", "RESEARCH", "gamedata.base", None],
            "b-turret": ["Harpoon", "Гарпун", "BUILDING", "gamedata.base", None],
            "b-bench": ["Bench", "Верстак", "BUILDING", "gamedata.base", None],
            "fn-craft": ["Craft", "Крафт", "BUILDING_FUNCTIONALITY", "gamedata.base", None],
            "bg-1": ["Border Zone", "Пограничная зона", "BIOME_GROUP", "gamedata.base", None],
        }
        prod_records = {
            "production": {
                "functionalities": [{"sid": "fn-craft", "name": "Craft", "int": {"function": 7, "stat used": 62},
                                     "float": {"production mult": 1.2}}],
                "farms": [],
                "buildings": [
                    {
                        "sid": "b-turret", "name": "Harpoon", "files": ["gamedata.base"],
                        "string": {"building category": "Turrets"}, "bool": {"is node": False, "is interior furniture": False},
                        "int": {"build materials": 0},
                        # Намеренно не по алфавиту: m-iron (слот 0), m-elec (слот 1)
                        "construction_order": [["m-iron", [6, 0, 0]], ["m-elec", [2, 0, 0]]],
                        "refs": {"functionality": [["fn-craft", [0, 0, 0]]]},
                    },
                    {
                        "sid": "b-bench", "name": "Bench", "files": ["gamedata.base"],
                        "string": {"building category": "Crafting"}, "bool": {"is node": False, "is interior furniture": True},
                        "int": {"build materials": 3, "power output": -5, "max operators": 1},
                        "refs": {"functionality": [["fn-craft", [0, 0, 0]]]},
                    },
                ],
                "research": [
                    {"sid": "res-1", "name": "Tech 1", "files": ["gamedata.base"], "string": {"category": "TECH"},
                     "int": {"level": 1, "time": 10}, "bool": {"is level upgrade": True},
                     "refs": {"enable buildings": [["b-bench", [0, 0, 0]]]}},
                    {"sid": "res-2", "name": "Tech 2", "files": ["gamedata.base"], "string": {"category": "SMITH"},
                     "int": {"level": 2, "time": 20},
                     "refs": {"requirements": [["res-1", [1, 0, 0]]], "enable buildings": [["b-turret", [0, 0, 0]]],
                              "enable weapon type": [["476-gamedata.base", [0, 0, 0]]]}},
                    {"sid": "res-3", "name": "Tech 3", "files": ["gamedata.base"], "string": {"category": "SMITH"},
                     "int": {"level": 3, "time": 30},
                     "refs": {"requirements": [["res-2", [1, 0, 0]]]}},
                ],
                "biome_resources": [
                    {"sid": "bg-1", "name": "Border Zone", "files": ["gamedata.base"], "int": {"index": 12},
                     "float": {"acidic water": 0.0, "acidic ground": 0.0, "farm water usage": 1.0},
                     "env_resources": {"int": {"farming altitude min": 0, "farming altitude max": 100},
                                       "float": {"arid": 1.0, "green": 0.2, "iron mult": 1.0, "copper mult": 0.6}},
                     "seasons_detail": [{"sid": "s-1", "name": "Dry", "duration_weight": 100,
                                         "weathers": [{"sid": "w-1", "name": "wind", "prob": 100,
                                                       "wind_speed_min": 0.6, "wind_speed_max": 1.0,
                                                       "affect_type": 1, "effect_affect_types": []}]}]},
                ],
            },
        }
        cur = mini_snapshot()
        cur["research"] = {"tech_level": 2, "completed": [{"sid": "res-1", "repeat_level": 1}],
                           "in_progress": [{"sid": "res-2", "progress_raw": 0.5}], "queue": [{"sid": "res-3"}]}
        cur["characters"][0]["jobs"]["primary"] = [
            {"task_name": "OPERATE_MACHINERY", "target_handle": [35, 0, 99, 0, 0], "target_name": "Верстак (Хранение)"},
        ]
        cur["property"]["buildings"][0]["interior"] = [{
            "id": "b-int-1", "sid": "35-0-99", "zone": "24.40", "name": "Верстак", "template_sid": "b-bench",
            "handle": [35, 0, 99, 0, 0], "floor": 0, "complete": True, "power_on": True, "production_progress": 0.25,
            "craft_queue": [{"item_sid": "476-gamedata.base", "item_name": "Катана", "progress": 0.25, "crit": 0}],
            "consumption": {"consumption amount-0": 1.5},
        }]
        cur["property"]["buildings"].append({
            "id": "b-tur-1", "zone": "24.40", "name": "Гарпун", "template_sid": "b-turret", "town": "Деревня",
            "complete": False, "construction_deposited": [4.0, 2.0],
        })
        wref = {"locations": [{"instance_id": "loc-1", "zone": "14.2", "name_ru": "Библиотека", "region": "Border Zone"}]}
        files = build_tables(cur, None, SNAPSHOT_ID, prod_idx, prod_records, world_ref=wref)

        b_rows = {r["instance_id"]: r for r in parse(files, "buildings.csv")}
        self.assertEqual(b_rows["b-tur-1"]["construction_materials"], "Железные листы: 4/6; Электродетали: 2/2")
        self.assertEqual(b_rows["b-int-1"]["active_craft"], "Катана (25%)")
        self.assertEqual(b_rows["b-int-1"]["assigned_workers"], "Кан (OPERATE_MACHINERY)")

        r_rows = {r["sid"]: r for r in parse(files, "research.csv")}
        self.assertEqual((r_rows["res-1"]["status"], r_rows["res-2"]["status"], r_rows["res-3"]["status"]),
                         ("completed", "in_progress", "queued"))
        self.assertEqual(r_rows["res-2"]["enable_crafts_count"], "1")

        c_rows = {r["sid"]: r for r in parse(files, "build_catalog.csv")}
        self.assertEqual((c_rows["b-bench"]["unlock_status"], c_rows["b-turret"]["unlock_status"]), ("unlocked", "in_queue"))
        self.assertEqual(c_rows["b-bench"]["construction_materials"], "Стройматериалы: 3")

        bio_rows = parse(files, "biome_resources.csv")
        self.assertEqual((bio_rows[0]["biome_sid"], bio_rows[0]["iron_mult"], bio_rows[0]["wind_speed_weighted_avg"]),
                         ("bg-1", "1", "0.8"))


class CalcColumnsTests(unittest.TestCase):
    def test_calc_match_game_cards(self) -> None:
        """Ловит дрейф формул itemstats/prosthetics и состава calc_* относительно карточек игры; без level - пусто."""
        char = {"id": "ps:1", "name": "Т", "weapons": [{"slot": "weapon_back", "sid": "katana", "type": "melee", "level": 70}],
                "armour": {"shirt": {"slot": "shirt", "sid": "chain", "level": 80},
                           "legs": {"slot": "legs", "sid": "chain", "level": None}},
                "prosthetics": [{"limb": "left_leg", "sid": "leg", "level": 95}],
                "items": [{"section": "back", "container": "inventory", "base_data_sid": "katana", "company_sid": "edge"}]}
        files = build_tables({"characters": [char]}, None, SNAPSHOT_ID, CALC_INDEX, None)
        rows = parse(files, "equipment.csv")
        by_kind = {r["kind"]: r for r in rows if r["level"]}
        for kind, expected in CALC_EXPECTED.items():
            with self.subTest(kind=kind):
                self.assertEqual({k: by_kind[kind][k] for k in expected}, expected)
        unknown = [r for r in rows if not r["level"]][0]
        self.assertEqual([k for k, v in unknown.items() if k.startswith("calc_") and v and k != "calc_status"], [])
        text = files["index.md"].decode("utf-8")
        self.assertIn("| `calc_swimming_mult` |", text)
        self.assertIn("| `calc_shot_speed_ms` |", text)
        self.assertIn("unverified_class", text)


def _armour_tpl(**kw: Any) -> list[Any]:
    base = {"slot": 5, "cut into stun": 0.4000000059604645, "level bonus": 0, "blunt def bonus": 0.0, "cut def bonus": 0.0,
            "pierce def mult": 1.0, "weight kg": 1.0, "stealth mult": 1.0, "ranged skill mult": 1.0, "dodge mult": 1.0,
            "dexterity mult": 1.0, "athletics mult": 1.0, "combat speed mult": 1.0, "unarmed bonus": 0}
    return ["Item", "Предмет", "ARMOUR", "gamedata.base", base | kw]


CARD_INDEX = {
    **CALC_INDEX,
    "const": ["Constants", "Constants", "CONSTANTS", "gamedata.base",
              {"weapon inventory weight mult": 0.5, "bow damage 1": 1.100000023841858, "bow damage 99": 1.2999999523162842}],
    # Шаблоны из справочника игры (Newwworld/rebirth/gamedata.base), день 134.
    "boots": _armour_tpl(**{"class": 2, "material type": 3, "slot": 9, "cut into stun": 0.20000000298023224,
                            "level bonus": -10, "weight kg": 4.0, "stealth mult": 1.100000023841858,
                            "refs:part coverage": {"p1": [40, 0, 0], "p2": [40, 0, 0]}}),
    "hat": _armour_tpl(**{"class": 2, "material type": 3, "slot": 3, "cut into stun": 0.20000000298023224,
                          "weight kg": 3.0, "weather protection amount": 0.3499999940395355, "weather protection0": 2,
                          "perception bonus": 4, "refs:part coverage": {"head": [70, 100, 0]}}),
    "mask": _armour_tpl(**{"class": 1, "material type": 1, "slot": 3, "weight kg": 1.0, "weather protection amount": 1.0,
                           "weather protection0": 1, "weather protection1": 4, "stealth mult": 1.100000023841858,
                           "refs:part coverage": {"head": [70, 0, 0]}}),
    "cloth": _armour_tpl(**{"class": 0, "material type": 0, "slot": 8, "refs:part coverage": {"p1": [100, 0, 0]}}),
    "sun": ["Falling Sun", "Падающее солнце", "WEAPON", "rebirth.mod",
            {"cut damage multiplier": 1.2000000476837158, "blunt damage multiplier": 1.0, "weight kg": 10.0,
             "weight mult": 1.0, "skill category": 3}],
    "smiths": ["Skeleton Smiths", "Скелеты-кузнецы", "WEAPON_MANUFACTURER", "gamedata.base",
               {"cut damage mod": 1.0, "blunt damage mod": 1.0, "weight mod": 1.0}],
    "catun_scrap": ["Catun Scrapmaster", "Катунский Кулибин", "WEAPON_MANUFACTURER", "gamedata.base",
                    {"cut damage mod": 1.0, "blunt damage mod": 1.0, "weight mod": 1.0}],
    "tooth": ["Tooth Pick", "Зубочистка", "CROSSBOW", "Newwworld.mod",
              {"value": 1000, "value 1": 15000, "pierce damage min 0": 10.0, "pierce damage min 1": 15.0,
               "pierce damage max 0": 20.0, "pierce damage max 1": 25.0, "reload time min": 2.0, "reload time min 1": 1.25,
               "reload time max": 3.0, "reload time max 1": 3.0, "accuracy deviation at 0 skill": 11.0,
               "accuracy deviation at 0 skill 1": 6.0, "range": 375, "range 1": 500, "shot speed": 350.0,
               "shot speed 1": 450.0, "accuracy perfect skill": 90, "weight kg": 4.0}],
    "eagle": ["Eagle Cross", "Орлиный крест", "CROSSBOW", "Newwworld.mod",
              {"value": 2500, "value 1": 34000, "pierce damage min 0": 50.0, "pierce damage min 1": 60.0,
               "pierce damage max 0": 70.0, "pierce damage max 1": 100.0, "reload time min": 5.0, "reload time min 1": 4.0,
               "reload time max": 10.0, "reload time max 1": 8.0, "accuracy deviation at 0 skill": 2.5,
               "accuracy deviation at 0 skill 1": 1.5, "range": 1500, "range 1": 1900, "shot speed": 700.0,
               "shot speed 1": 800.0, "accuracy perfect skill": 80, "weight kg": 18.0}],
}
# (персонаж, слот, sid, level, значения карточки). None - не сверяется: расхождение на границе 59.x у Высокого
# уровня (карточка на 1 ниже расчёта, не подтверждено) и скрытая на карточке атлетика L95 (порог не установлен).
ARMOUR_CARDS: list[tuple[str, str, str, int, dict[str, float | None]]] = [
    ("chain80", "shirt", "chain", 80, {"blunt": 19, "cut": 42, "harpoon": 27, "weight": 10, "stealth": 0.71,
                                       "ranged": 0.70, "dodge": 0.90, "combat_speed": 0.97, "dexterity": 0.85}),
    ("chain60", "shirt", "chain", 60, {"blunt": 15, "cut": 33, "harpoon": None, "weight": 10, "stealth": 0.63}),
    ("boots95", "boots", "boots", 95, {"blunt": 45, "cut": 66, "harpoon": 76, "weight": 4, "stealth": 0.69,
                                       "athletics": None}),
    ("boots60", "boots", "boots", 60, {"blunt": None, "cut": 42, "harpoon": None, "weight": 4, "stealth": 0.45,
                                       "athletics": 0.93}),
    ("hat80", "head", "hat", 80, {"blunt": 43, "cut": 63, "harpoon": 71, "weight": 4, "stealth": 1.0}),
    ("mask60", "head", "mask", 60, {"blunt": 16, "cut": 26, "harpoon": None, "weight": 1, "stealth": 1.10}),
]
# sid, level, урон min-max, разброс, перезарядка min-max, дальность, скорость м/с, цена карточки.
CROSSBOW_CARDS = {
    "tooth20": ("tooth", 20, (12, 24), 10.0, (1.85, 3.0), 40, 85, 1560),
    "eagle95": ("eagle", 95, (76, 127), 1.55, (4.05, 8.1), 188, 183, 30928),
}


def ui_trunc(cell_text: str) -> int:
    """Правило интерфейса: усечение до целого (допуск на запись CSV с 6 знаками)."""
    return math.floor(float(cell_text) + 1e-6)


def ui_round(cell_text: str) -> int:
    """Правило интерфейса: округление до целого (половина вверх)."""
    return math.floor(float(cell_text) + 0.5 + 1e-6)


class CalcCardBatchesTests(unittest.TestCase):
    def test_calc_match_card_batches(self) -> None:
        """Ловит дрейф формул брони, оружия и арбалета относительно карточек игры (float32 гарпуна, усечение,
        шлем без класса, скорость и цена арбалета) и состава calc_status."""
        chars: list[dict[str, Any]] = []
        for cid, slot, sid, level, _ in ARMOUR_CARDS:
            chars.append({"id": cid, "name": cid, "armour": {slot: {"slot": slot, "sid": sid, "level": level}}})
        chars.append({"id": "sun60", "name": "sun",
                      "weapons": [{"slot": "weapon_back", "sid": "sun", "type": "melee", "level": 60}],
                      "items": [{"section": "back", "container": "inventory", "base_data_sid": "sun",
                                 "company_sid": "catun_scrap"}]})
        # Тяжёлое x Скелеты-кузнецы: вес на карточке не сошёлся (23 vs 24.0) - сочетание не verified.
        chars.append({"id": "st_melee", "name": "s",
                      "weapons": [{"slot": "weapon_back", "sid": "sun", "type": "melee", "level": 60}],
                      "items": [{"section": "back", "container": "inventory", "base_data_sid": "sun",
                                 "company_sid": "smiths"}]})
        for cid, (sid, level, *_rest) in CROSSBOW_CARDS.items():
            chars.append({"id": cid, "name": cid,
                          "weapons": [{"slot": "weapon_back", "sid": sid, "type": "crossbow", "level": level}]})
        # По одному случаю на значение calc_status (verified - любая строка выше).
        chars += [
            {"id": "st_level", "name": "s", "armour": {"shirt": {"slot": "shirt", "sid": "chain", "level": None}}},
            {"id": "st_tpl", "name": "s", "armour": {"shirt": {"slot": "shirt", "sid": "ghost", "level": 80}}},
            {"id": "st_class", "name": "s", "armour": {"shirt": {"slot": "shirt", "sid": "cloth", "level": 80}}},
        ]
        files = build_tables({"characters": chars}, None, SNAPSHOT_ID, CARD_INDEX, None)
        rows = {r["character_id"]: r for r in parse(files, "equipment.csv")}
        names = {"stealth": "calc_stealth_mult", "ranged": "calc_ranged_mult", "dodge": "calc_dodge_mult",
                 "combat_speed": "calc_combat_speed_mult", "dexterity": "calc_dexterity_mult",
                 "athletics": "calc_athletics_mult"}
        for cid, _slot, _sid, _level, card in ARMOUR_CARDS:
            r = rows[cid]
            with self.subTest(card=cid):
                self.assertEqual(r["calc_status"], "verified")
                for key, col in (("blunt", "calc_blunt_resist_pct"), ("cut", "calc_cut_resist_pct"),
                                 ("harpoon", "calc_harpoon_resist"), ("weight", "calc_weight_kg")):
                    if card[key] is not None:  # карточка усекает целую часть неокруглённого значения
                        self.assertEqual(math.floor(float(r[col])), card[key], col)
                for key, col in names.items():
                    if card.get(key) is not None:
                        self.assertAlmostEqual(float(r[col]), card[key], delta=0.0051, msg=col)
        sun = rows["sun60"]
        self.assertEqual((round(float(sun["calc_cut_damage"]), 2), round(float(sun["calc_blunt_damage"]), 2)), (1.58, 1.2))
        const = CARD_INDEX["const"][4]

        def card_price(cid: str) -> int:
            # Цена карточки в публикацию не идёт (цены - item_prices.csv); формула цены сверяется только здесь.
            sid, level = CROSSBOW_CARDS[cid][:2]
            return itemstats.crossbow_stats(CARD_INDEX[sid][4], level, const)["card_value"]

        for cid, (_sid, _lv, dmg, spread, reload_s, rng, speed, value) in CROSSBOW_CARDS.items():
            r = rows[cid]
            with self.subTest(card=cid):
                # Правила интерфейса применяются к неокруглённым значениям: min и цена усекаются, max и скорость округляются.
                self.assertEqual((ui_trunc(r["calc_damage_min"]), ui_round(r["calc_damage_max"])), tuple(dmg))
                self.assertEqual((float(r["calc_spread_deg"]), float(r["calc_reload_min_s"]), float(r["calc_reload_max_s"]),
                                  float(r["calc_range_m"]), ui_round(r["calc_shot_speed_ms"]), card_price(cid)),
                                 (spread, reload_s[0], reload_s[1], float(rng), speed, value))
        for cid, status in (("st_level", "unknown_level"), ("st_tpl", "unknown_template"), ("st_class", "unverified_class"),
                            ("st_melee", "unverified_class")):
            with self.subTest(status=status):
                self.assertEqual(rows[cid]["calc_status"], status)
                self.assertEqual([k for k, v in rows[cid].items() if k.startswith("calc_") and v and k != "calc_status"], [])
        # Любая строка с calc_status != verified не содержит ни одного значения calc_*; цен в calc_* нет.
        for r in rows.values():
            if r["calc_status"] != "verified":
                self.assertEqual([k for k, v in r.items() if k.startswith("calc_") and k != "calc_status" and v], [], r["character_id"])
        self.assertNotIn("calc_card_value", rows["eagle95"])

        # Шаблон с несколькими кодами погоды и шлем без поправок класса.
        view = itemstats.armour_view(CARD_INDEX["mask"][4], 60, lambda sid: sid)
        self.assertEqual(view["weather_protection"], {"sand_storm": 1.0, "gas": 1.0})
        self.assertEqual(view["skill_modifiers"]["stealth_multiplier"], 1.1)


class InstanceTablesTests(unittest.TestCase):
    def test_instances_stay_separate_in_every_place(self) -> None:
        """Ловит склейку экземпляров разного уровня в storage, потерю запасных и складских предметов в equipment,
        сдвиг прежних worn-строк и зависимость inventory (ключ, dup_no) от порядка предметов."""
        idx = {**INDEX, "bp": ["Backpack", "Рюкзак", "CONTAINER", "x.mod", None]}

        def item(sid: str, section: str, container: str = "inventory", **kw: Any) -> dict[str, Any]:
            return {"base_data_sid": sid, "section": section, "container": container, "quality": 100.0,
                    "item_sid": kw.pop("item_sid", f"i-{sid}-{section}-{kw.get('level')}-{kw.get('quantity')}"), **kw}

        items = [
            item("476-gamedata.base", "back", level=40, item_sid="w0"),
            item("bp", "backpack_attach", level=0, item_sid="bp0"),
            item("476-gamedata.base", "main", level=55, item_sid="w1"),
            item("476-gamedata.base", "backpack_content", "inventory>bp0", level=None, item_sid="w2"),
            item("1965-gamedata.base", "main", level=0, quantity=1, item_sid="h1"),
            item("1965-gamedata.base", "main", level=0, quantity=3, item_sid="h2"),
        ]
        cur = mini_snapshot()
        cur["characters"][1]["items"] = items
        cur["characters"][1]["weapons"][0]["sid"] = "476-gamedata.base"
        cur["property"]["buildings"][0]["storage_items"] = [
            {"base_data_sid": "476-gamedata.base", "quantity": 1, "container": "storage>c1", "level": 40, "quality": 100.0},
            {"base_data_sid": "476-gamedata.base", "quantity": 1, "container": "storage>c1", "level": 60, "quality": 100.0},
            {"base_data_sid": "1965-gamedata.base", "quantity": 11, "container": "storage>c1", "level": 0, "quality": 1.0}]
        files = build_tables(cur, None, SNAPSHOT_ID, idx, RECORDS, world_ref=WORLD_REF)

        katana = [r for r in parse(files, "storage.csv") if r["item"] == "Катана"]
        self.assertEqual([(r["level"], r["quantity"]) for r in katana], [("40", "1"), ("60", "1")])
        equipment = [r for r in parse(files, "equipment.csv") if r["kind"] == "weapon"]
        self.assertEqual([(r["location_kind"], r["level"], r["calc_status"], r["container"]) for r in equipment],
                         [("worn", "40", "unverified_class", ""), ("inventory", "55", "unverified_class", ""),
                          ("backpack", "", "unknown_level", "Рюкзак"), ("storage", "40", "unverified_class", "c1"),
                          ("storage", "60", "unverified_class", "c1")])
        inventory = parse(files, "inventory.csv")
        self.assertEqual([(r["container"], r["slot"], r["base_sid"][:3], r["dup_no"]) for r in inventory
                          if r["character_id"] == "ps:1"],
                         [("body", "backpack", "bp", "1"), ("body", "weapon_back", "476", "1"), ("backpack", "backpack_content", "476", "1"),
                          ("inventory", "main", "196", "1"), ("inventory", "main", "196", "2"), ("inventory", "main", "476", "1")])
        shuffled = copy.deepcopy(cur)
        shuffled["characters"][1]["items"].reverse()
        again = build_tables(shuffled, None, SNAPSHOT_ID, idx, RECORDS, world_ref=WORLD_REF)
        self.assertEqual(files["inventory.csv"], again["inventory.csv"])

    def test_races_decode_weather_and_keep_unknown_codes(self) -> None:
        """Ловит подмену неизвестного кода погоды расшифровкой, потерю кода 0 (нет иммунитета) и источника перекрытия."""
        records = {"races": [
            {"sid": "r-1", "name": "Skel", "files": ["gamedata.base", "rebirth.mod"],
             "int": {"weather immunity0": 1, "weather immunity1": 5, "speed max skill": 110},
             "field_files": {"weather immunity0": "rebirth.mod", "weather immunity1": "rebirth.mod", "speed max skill": "gamedata.base"},
             "weather_by_file": {"gamedata.base": [0], "rebirth.mod": [1, 5]}},
            {"sid": "r-2", "name": "Human", "files": ["gamedata.base"], "int": {"weather immunity0": 0},
             "field_files": {"weather immunity0": "gamedata.base"}, "weather_by_file": {"gamedata.base": [0]}}]}
        rows = {r["sid"]: r for r in parse(build_tables(mini_snapshot(), None, SNAPSHOT_ID, INDEX, records), "races.csv")}
        self.assertEqual((rows["r-1"]["weather_immunity_codes"], rows["r-1"]["weather_immunity"],
                          rows["r-1"]["weather_immunity_by_file"], rows["r-1"]["speed_max_skill"]),
                         ("1; 5", "песчаная буря; код 5 (не установлен)", "gamedata.base=0; rebirth.mod=1/5", "110"))
        self.assertEqual((rows["r-1"]["overridden_fields"], rows["r-2"]["weather_immunity"], rows["r-2"]["overridden_fields"]),
                         ("weather immunity0=rebirth.mod; weather immunity1=rebirth.mod", "нет", ""))


class TableValidationTests(unittest.TestCase):
    def test_manifest_and_index_mismatches(self) -> None:
        """Манифест ссылается на отсутствующую таблицу или число строк расходится с index.md - публикация отклоняется."""
        files = build_tables(mini_snapshot(), None, SNAPSHOT_ID, INDEX, RECORDS)
        manifest = {"snapshot_id": SNAPSHOT_ID, "artifacts": {"tables": "tables/index.md"}}

        def package(mutate: Any) -> str:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "tables"
                out.mkdir()
                local = dict(files)
                mutate(local)
                for name, data in local.items():
                    (out / name).write_bytes(data)
                try:
                    validate_tables(Path(tmp), manifest)
                except PublicationValidationError as error:
                    return str(error)
                return "ok"

        self.assertEqual(package(lambda f: None), "ok")
        self.assertIn("не найдена", package(lambda f: f.pop("diplomacy.csv")))
        self.assertIn("в файле", package(lambda f: f.update({"unique_npcs.csv": f["unique_npcs.csv"] + b"x,,,,,\n"})))
        self.assertIn("не перечислены", package(lambda f: f.update({"extra.csv": b"a\n1\n"})))
        self.assertIn("другому снимку", package(lambda f: f.update(
            {"index.md": f["index.md"].replace(SNAPSHOT_ID.encode(), b"sha256-other")})))

    def test_unique_entities_table(self) -> None:
        """Проверяет структуру, колонки, форматирование строк и сортировку таблицы unique_entities.csv."""
        t_empty = unique_entities_table(None)
        self.assertEqual(len(t_empty.rows), 0)
        self.assertEqual([c for c, _ in t_empty.columns], GOLDEN_HEADERS["unique_entities.csv"])

        sample_entities = {
            "unique_npcs": [{
                "npc_sid": "npc-1", "name_en": "Npc One", "name_ru": "НПС Один",
                "category": "recruit",
                "locations": [{"instance_id": "loc-1", "resolution": "unique"}],
                "dynamic": {"state_code": 1, "squad_status": "confirmed_in_squad", "character_id": "ps:1"},
            }],
            "bosses": [{
                "npc_sid": "boss-1", "name_en": "Boss One", "name_ru": "Босс Один",
                "classification_origin": "game_bounty",
                "static_locations": [{"instance_id": "loc-2", "resolution": "ambiguous"}],
                "observed_state": {"state_code": 0},
                "cleared_evidence": {"location_instance_id": "loc-2", "cleared_at": "Day 10 12:00", "source": "session-1"},
            }],
            "meitou_weapons": [{
                "base_sid": "blade-1", "name_en": "Meitou Blade", "name_ru": "Клинок Мейто",
                "static_holders": [{"town_instance_id": "loc-2", "npc_sid": "boss-1"}],
                "observed_instances": [{
                    "location_kind": "equipped", "carrier_name": "Хот Лон",
                }],
                "provenance_status": "confirmed_trophy",
            }],
        }
        t = unique_entities_table(sample_entities)
        self.assertEqual(len(t.rows), 3)
        # Порядок сортировки: boss, meitou, npc
        kinds = [r["entity_kind"] for r in t.rows]
        self.assertEqual(kinds, ["boss", "meitou", "npc"])

        boss_row = t.rows[0]
        self.assertEqual(boss_row["sid"], "boss-1")
        self.assertEqual(boss_row["primary_location_instance_id"], "loc-2")
        self.assertEqual(boss_row["location_resolution"], "ambiguous")
        self.assertEqual(boss_row["provenance_status"], "session-1")
        self.assertEqual(boss_row["state_code"], 0)

        meitou_row = t.rows[1]
        self.assertEqual(meitou_row["sid"], "blade-1")
        self.assertEqual(meitou_row["meitou_base_sid"], "blade-1")
        self.assertIsNone(meitou_row["primary_location_instance_id"])
        self.assertIsNone(meitou_row["location_resolution"])
        self.assertEqual(meitou_row["carrier_or_storage"], "equipped:Хот Лон")
        self.assertEqual(meitou_row["provenance_status"], "confirmed_trophy")
        self.assertIsNone(meitou_row["state_code"])

        npc_row = t.rows[2]
        self.assertEqual(npc_row["sid"], "npc-1")
        self.assertEqual(npc_row["squad_status"], "confirmed_in_squad")
        self.assertEqual(npc_row["character_id"], "ps:1")
        self.assertEqual(npc_row["primary_location_instance_id"], "loc-1")
        self.assertEqual(npc_row["location_resolution"], "unique")

    def test_unique_entities_table_ambiguous_and_boss_sync(self) -> None:
        """Проверяет разрешение неоднозначных локаций и синхронизацию динамических полей босса."""
        entities = {
            "unique_npcs": [
                {
                    "npc_sid": "hamut", "name_en": "Hamut", "name_ru": "Хамут",
                    "category": "recruit",
                    # 5 разных городов
                    "locations": [
                        {"instance_id": f"loc-{i}", "resolution": "unique"}
                        for i in range(1, 6)
                    ],
                    "dynamic": {"state_code": 1, "squad_status": "confirmed_in_squad", "character_id": "ps:hamut"},
                },
                {
                    "npc_sid": "agnu", "name_en": "Agnu", "name_ru": "Агну",
                    "category": "recruit",
                    "locations": [{"instance_id": "loc-tower", "resolution": "unique"}],
                    "dynamic": {"state_code": 1, "squad_status": "confirmed_in_squad", "character_id": "ps:agnu"},
                },
            ],
            "bosses": [
                {
                    "npc_sid": "agnu", "name_en": "Agnu", "name_ru": "Агну",
                    "category": "recruit",
                    "classification_origin": "world_state",
                    "static_locations": [{"instance_id": "loc-tower", "resolution": "unique"}],
                    "observed_state": {"state_code": 1, "player_defeat_flag": False, "captured_by": None},
                    "cleared_evidence": None,
                    "personal_evidence": None,
                },
            ],
            "meitou_weapons": [
                {
                    "base_sid": "paladins_cross", "name_en": "Paladin's Cross", "name_ru": "Крест паладина",
                    "static_holders": [{"town_instance_id": "loc-blister", "npc_sid": "phoenix"}],
                    "observed_instances": [{"location_kind": "equipped", "carrier_name": "Гриффин"}],
                    "observation_status": "observed",
                    "provenance_status": "unconfirmed",
                },
            ],
        }
        t = unique_entities_table(entities)
        rows_by_sid = {(r["entity_kind"], r["sid"]): r for r in t.rows}

        # 1. Хамут: 5 городов -> ambiguous, primary_location_instance_id: None
        hamut_row = rows_by_sid[("npc", "hamut")]
        self.assertIsNone(hamut_row["primary_location_instance_id"])
        self.assertEqual(hamut_row["location_resolution"], "ambiguous")

        # 2. Агну (boss): динамические поля (squad_status, character_id) получены согласованно с NPC
        agnu_row = rows_by_sid[("boss", "agnu")]
        self.assertEqual(agnu_row["state_code"], 1)
        self.assertEqual(agnu_row["squad_status"], "confirmed_in_squad")
        self.assertEqual(agnu_row["character_id"], "ps:agnu")
        self.assertEqual(agnu_row["primary_location_instance_id"], "loc-tower")
        self.assertIsNone(agnu_row["provenance_status"])

        # 3. Мейто Крест паладина: без локации владельца, provenance_status: unconfirmed
        meitou_row = rows_by_sid[("meitou", "paladins_cross")]
        self.assertIsNone(meitou_row["primary_location_instance_id"])
        self.assertIsNone(meitou_row["location_resolution"])
        self.assertEqual(meitou_row["provenance_status"], "unconfirmed")



class PublishTablesTests(unittest.TestCase):
    def test_publish_writes_tables_and_repeats_bytes(self) -> None:
        """Публикация кладёт tables/ и регистрирует артефакт; повтор даёт те же байты; нехватка игры попадает в warnings."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            published = root / "published"
            published.mkdir()
            (published / ".gitkeep").write_bytes(b"")
            _create_sample_snapshot(root / "snapshots", "snap-tables")
            kwargs = dict(snapshot_id="snap-tables", snapshots_dir=root / "snapshots", diffs_dir=root / "diffs",
                          published_dir=published, generated_at="2026-11-06T00:00:00Z", game_cache_dir=root / "cache")
            with patch("tools.publisher.DEFAULT_INDEX_PATH", root / "index.json"):
                first = publish(**kwargs)
                before = {p.name: p.read_bytes() for p in (published / "tables").iterdir()}
                publish(**kwargs)
            after = {p.name: p.read_bytes() for p in (published / "tables").iterdir()}
            self.assertEqual(before, after)
            self.assertEqual(first["manifest"]["artifacts"]["tables"], "tables/index.md")
            self.assertTrue(any(w.startswith("tables:") for w in first["warnings"]))
            self.assertEqual(validate_publication(published)["status"], "passed")
            brief = (published / "brief.md").read_text(encoding="utf-8")
            self.assertIn("tables/index.md", brief)


if __name__ == "__main__":
    unittest.main()
