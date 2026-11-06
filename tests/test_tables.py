"""Табличные выгрузки published/tables/: схема колонок, порядок строк, валидация манифеста, публикация."""
from __future__ import annotations

import copy
import csv
import io
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.test_publish import _create_sample_snapshot
from tools.publisher import publish
from tools.tables import build_tables
from tools.validation import PublicationValidationError, validate_publication, validate_tables

SNAPSHOT_ID = "sha256-mini"

# Эталон колонок небольших таблиц: изменение схемы - осознанное, с обновлением словаря index.md и контракта.
GOLDEN_HEADERS: dict[str, list[str]] = {
    "unique_npcs.csv": ["sid", "name_en", "name_ru", "state_code", "player", "captured_by"],
    "diplomacy.csv": ["faction_sid", "faction_name", "relation", "trust", "trust_neg"],
    "town_overrides.csv": ["replacement_sid", "town", "raw_name", "replacement_name", "replacement_name_ru",
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
