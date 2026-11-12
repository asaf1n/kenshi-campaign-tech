"""Регрессии реестра локаций на изолированных входах."""
from __future__ import annotations

from copy import deepcopy
import os
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import patch

from tools.current_layers import public_projection
from tools.io_utils import ExtractionError, canonical, digest, file_hash, tree_hashes
from tools.location_registry import (
    build_registry, merge_progress, read_discovery_save, registry_bytes, resolve_template_factions,
    upgrade_progress, validate_progress,
    validate_registry,
)
from tools.save_reader import extract_world_state


def _world(ids: Iterable[str], world_ref_id: str = "world-new") -> dict:
    return {"world_ref_id": world_ref_id,
            "regions": [{"name_en": "Greenbeach", "name_ru": "Зелёный пляж"}], "locations": [
        {"instance_id": sid, "template_sid": f"template-{sid}", "name_en": sid, "name_ru": None,
         "kind": "town", "x": 0, "z": 0, "region": "Greenbeach"}
        for sid in ids
    ]}


def _progress(rows: list[dict], world_ref_id: str = "world-new") -> dict:
    return {"format": "kenshi-location-progress/3", "campaign_id": "campaign",
            "world_ref_id": world_ref_id, "locations": rows}


def _mark(sid: str, game_time: str | None = None) -> dict:
    row = {"instance_id": sid, "evidence": "S-20261109-1"}
    if game_time is not None:
        row["game_time"] = game_time
    return row


class LocationRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {"snapshot_id": "head", "campaign_id": "campaign",
                         "world_ref_id": "world-new", "game_time": "Day 13 08:00"}

    def test_registry_holds_reference_statics_and_discovery_from_save(self) -> None:
        ids = ("known", "unknown", "absent")
        world = _world(ids)
        registry = build_registry(self.manifest, world, discovery={"known": True, "unknown": False})
        validate_registry(registry, self.manifest, world)
        rows = {row["instance_id"]: row for row in registry["locations"]}
        self.assertEqual(set(rows), set(ids))
        self.assertTrue(all(row["region"] == "Зелёный пляж" for row in rows.values()))
        self.assertTrue(all(row["region"] == "Greenbeach" for row in world["locations"]))
        self.assertTrue(rows["known"]["discovered"])
        self.assertFalse(rows["unknown"]["discovered"])
        self.assertNotIn("discovered", rows["absent"])
        self.assertFalse(any("cleared" in row for row in rows.values()))
        rebuilt = build_registry(self.manifest, _world(reversed(ids)), discovery={"unknown": False, "known": True})
        self.assertEqual(registry_bytes(registry), registry_bytes(rebuilt))
        for label, mutate, error in [
            ("чужой выпуск", lambda r: r.update(snapshot_id="other"), "другому выпуску"),
            ("неполный набор", lambda r: r["locations"].pop(), "не все локации"),
            ("статика", lambda r: r["locations"][0].update(kind="ruins"), "Статические поля"),
            ("английский регион", lambda r: r["locations"][0].update(region="Greenbeach"), "Статические поля"),
        ]:
            with self.subTest(label):
                changed = deepcopy(registry)
                mutate(changed)
                with self.assertRaisesRegex(ValueError, error):
                    validate_registry(changed, self.manifest, world)

    def test_region_translation_preserves_values_without_translation(self) -> None:
        cases = [
            ("The Swamp", [{"name_en": "The Swamp", "name_ru": "Болото"}], "Болото"),
            ("Greenbeach", [{"name_en": "Greenbeach", "name_ru": None}], "Greenbeach"),
            ("Greenbeach", [{"name_en": "Greenbeach", "name_ru": ""}], "Greenbeach"),
            ("Unknown", [{"name_en": "Greenbeach", "name_ru": "Зелёный пляж"}], "Unknown"),
            (None, [{"name_en": "Greenbeach", "name_ru": "Зелёный пляж"}], None),
            ("Greenbeach", None, "Greenbeach"),
        ]
        for region, translations, expected in cases:
            with self.subTest(region=region, translations=translations):
                world = _world(["town"])
                world["locations"][0]["region"] = region
                if translations is None:
                    world.pop("regions")
                else:
                    world["regions"] = translations
                registry = build_registry(self.manifest, world)
                self.assertEqual(registry["locations"][0]["region"], expected)
                validate_registry(registry, self.manifest, world)

    def test_template_factions_translate_by_sid_then_msgid(self) -> None:
        world = _world(["sid", "msgid", "missing"])
        records = {}
        expected = {}
        for key, name_en, name_ru in [
            ("sid", "Faction A", "Фракция А"),
            ("msgid", "Faction B", "Фракция Б"),
            ("missing", "Faction C", None),
        ]:
            faction_sid = f"faction-{key}"
            records[f"template-{key}"] = {"type_id": 13, "refs": {"faction": {faction_sid: {}}}}
            records[faction_sid] = {"type_id": 10, "name": name_en}
            expected[f"template-{key}"] = {"sid": faction_sid, "name_en": name_en, "name_ru": name_ru}
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            source = game / "gamedata.base"
            source.write_bytes(b"game database fixture")
            world.update(mods=["gamedata.base"], source_hashes={"gamedata.base": file_hash(source)})
            with (
                patch.dict(os.environ, {"KENSHI_DIR": str(game), "KENSHI_WORKSHOP_DIR": str(game / "workshop")}),
                patch("tools.location_registry.gamedb.active_files",
                      return_value={"missing": [], "files": [str(source)]}),
                patch("tools.location_registry.gamedb.build", return_value={"records": records}),
                patch("tools.location_registry.gamedb.load_po", return_value=(
                    {"faction-sid": "Фракция А", "faction-msgid": ""}, {},
                    {"Faction A": "Другой перевод", "Faction B": "Фракция Б"},
                )),
            ):
                factions = resolve_template_factions(world)
        self.assertEqual(factions, expected)
        registry = build_registry(self.manifest, world, factions=factions)
        validate_registry(registry, self.manifest, world)
        for row in registry["locations"]:
            self.assertEqual(row["template_faction"], expected[f"template-{row['instance_id']}"])

    def test_progress_transitions_preserve_orphans_and_reject_invalid_changes(self) -> None:
        world = _world(["town"])
        historical = _mark("orphan")
        previous = _progress([_mark("town"), historical], world_ref_id="world-old")
        cases = [
            ("сохранённый ID", previous, None),
            ("новый неизвестный ID", _progress([*previous["locations"], _mark("bogus")], "world-old"),
             "Неизвестный instance_id"),
            ("отметка удалена", _progress([historical], "world-old"), "удалена"),
            ("повтор instance_id", _progress([*previous["locations"], _mark("town")], "world-old"), "Повтор отметки"),
            ("неизвестный ID в том же мире", _progress(previous["locations"]), "Неизвестный instance_id"),
        ]
        for label, progress, error in cases:
            with self.subTest(label):
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        validate_progress(progress, "campaign", world, previous)
                else:
                    self.assertEqual(validate_progress(progress, "campaign", world, previous), ["orphan"])

    def test_legacy_progress_keeps_only_explored_marks(self) -> None:
        """/1 и /2 из истории Git: отметкой зачистки становится explored, «посещено» снимается без нарушения базы."""
        world = _world(["town", "lab"])
        legacy_rows = [{"instance_id": "town", "status": "visited", "evidence": "День 7"},
                       {"instance_id": "lab", "status": "explored", "evidence": "День 97", "game_time": "Day 9 10:00"}]
        expected = _progress([{"instance_id": "lab", "evidence": "День 97", "game_time": "Day 9 10:00"}])
        v2 = {**_progress(legacy_rows), "format": "kenshi-location-progress/2"}
        v1 = {**v2, "format": "kenshi-location-progress/1",
              "locations": [{**row, "branch_id": "line"} for row in legacy_rows]}
        for legacy in (v1, v2):
            with self.subTest(legacy["format"]):
                self.assertEqual(upgrade_progress(legacy), expected)
                self.assertEqual(validate_progress(expected, "campaign", world, legacy), [])
                with self.assertRaisesRegex(ValueError, "удалена"):
                    validate_progress(_progress([]), "campaign", world, legacy)

    def test_merge_applies_cleared_marks_only_up_to_release_time(self) -> None:
        world = _world(["lab", "untouched", "future", "historic"])
        registry = build_registry(self.manifest, world, discovery={"lab": True})
        progress = _progress([_mark("lab"), _mark("future", game_time="Day 13 08:01"),
                              _mark("historic", game_time="Day 13 08:00")])
        validate_progress(progress, "campaign", world)
        merged = merge_progress(registry, progress)
        validate_registry(merged, self.manifest, world)
        self.assertEqual({row["instance_id"]: row.get("cleared") for row in merged["locations"]}, {
            "lab": {"evidence": "S-20261109-1"}, "untouched": None, "future": None,
            "historic": {"evidence": "S-20261109-1", "game_time": "Day 13 08:00"},
        })
        self.assertEqual(merge_progress(merged, None), registry)
        late = deepcopy(merged)
        next(row for row in late["locations"] if row["instance_id"] == "lab")["cleared"]["game_time"] = "Day 14 00:00"
        with self.assertRaisesRegex(ValueError, "позже времени выпуска"):
            validate_registry(late, self.manifest, world)

    def test_discovery_uses_only_unique_town_instances_and_verified_save(self) -> None:
        records = [
            {"type": 94, "string": {"instance": "town"}, "bool": {"discovered": False, "explored": True}},
            {"type": 94, "string": {"instance": "duplicate"}, "bool": {"discovered": True}},
            {"type": 94, "string": {"instance": "duplicate"}},
            {"type": 94, "string": {"instance": "unknown"}, "bool": {"explored": True}},
            {"type": 13, "string": {"instance": "template"}, "bool": {"discovered": True}},
        ]
        world_state = extract_world_state(records)
        self.assertEqual(world_state["town_discovery"], [{"instance_id": "town", "discovered": False}])
        self.assertNotIn("town_discovery", public_projection({"world_state": world_state})["world_state"])
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp)
            quick = save / "quick.save"
            quick.write_bytes(b"synchronized save")
            expected = digest(canonical(tree_hashes(save)))
            with patch("tools.location_registry.read_file", return_value={"records": records}) as read:
                self.assertEqual(read_discovery_save(save, expected), {"town": False})
                with self.assertRaisesRegex(ExtractionError, "не совпадает"):
                    read_discovery_save(save, "0" * 64)
                self.assertEqual(read.call_count, 1)

            def changed(_: bytes) -> dict:
                quick.write_bytes(b"changed save")
                return {"records": records}

            with patch("tools.location_registry.read_file", side_effect=changed):
                with self.assertRaisesRegex(ExtractionError, "изменился во время чтения"):
                    read_discovery_save(save, expected)
