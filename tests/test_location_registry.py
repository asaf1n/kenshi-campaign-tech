"""Регрессии реестра локаций на изолированных входах."""
from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import patch

from tools.current_layers import public_projection
from tools.io_utils import ExtractionError, canonical, digest, tree_hashes
from tools.location_registry import (
    build_registry, merge_progress, read_discovery_save, registry_bytes, validate_progress, validate_registry,
)
from tools.save_reader import extract_world_state


def _world(ids: Iterable[str], world_ref_id: str = "world-new") -> dict:
    return {"world_ref_id": world_ref_id, "locations": [
        {"instance_id": sid, "template_sid": f"template-{sid}", "name_en": sid, "name_ru": None,
         "kind": "town", "x": 0, "z": 0, "region": "region"}
        for sid in ids
    ]}


def _report(sid: str, characters: Iterable[tuple[str, str]] = (),
            buildings: Iterable[tuple[str, str]] = ()) -> dict:
    return {
        "snapshot_id": sid,
        "characters": [{"resolution_status": status, "location": {"instance_id": location}}
                       for location, status in characters],
        "buildings": [{"resolution_status": status, "location": {"instance_id": location}}
                      for location, status in buildings],
    }


def _progress(rows: list[dict], world_ref_id: str = "world-new") -> dict:
    return {"format": "kenshi-location-progress/1", "campaign_id": "campaign",
            "world_ref_id": world_ref_id, "locations": rows}


def _mark(sid: str, status: str, branch: str = "active", game_time: str | None = None) -> dict:
    row = {"instance_id": sid, "status": status, "evidence": "Подтверждение игрока", "branch_id": branch}
    if game_time is not None:
        row["game_time"] = game_time
    return row


class LocationRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {"snapshot_id": "head", "branch_id": "active", "campaign_id": "campaign",
                         "world_ref_id": "world-new", "game_time": "Day 13 08:00"}

    def test_only_retained_chain_and_in_location_count_as_visits(self) -> None:
        chain = {"head": ("rollback", 13, "active"), "rollback": ("discarded", 7, "active"),
                 "discarded": ("missing", 11, "active"), "missing": ("old", 2, "active"),
                 "old": ("foreign", 1, "common"), "foreign": (None, 0, "foreign"),
                 "side": ("old", 3, "other")}
        index = {"snapshots": {
            sid: {"parent_snapshot_id": parent, "branch_id": branch,
                  "campaign_id": "other" if sid == "foreign" else "campaign",
                  "game_time": {"day": day, "time": "8:00"}}
            for sid, (parent, day, branch) in chain.items()
        }}
        reports = {
            "old": _report("old", [("first", "in_location")] * 2),
            "discarded": _report("discarded", [("discarded", "in_location")]),
            "side": _report("side", [("side", "in_location")]),
            "foreign": _report("foreign", [("foreign", "in_location")]),
            "rollback": _report("rollback", [("near", "near_location")], [("base", "in_location")]),
        }
        head = _report("head", [("first", "in_location"), ("region", "in_region")])
        ids = ("near", "base", "first", "region", "side", "discarded", "foreign", "discovered")
        world = _world(ids)
        registry = build_registry(self.manifest, world, head, index, reports.get,
                                  discovery={"discovered": True, "first": False})
        validate_registry(registry, self.manifest, world, index_data=index)
        self.assertEqual(registry["coverage"], {"chain_snapshots": 4, "location_snapshots": 3})
        rows = {row["instance_id"]: row for row in registry["locations"]}
        self.assertEqual(len(rows), len(ids))
        self.assertEqual({sid for sid, row in rows.items() if row["auto_status"] == "visited"}, {"first", "base"})
        self.assertEqual(rows["first"]["presence"], {
            "first": {"snapshot_id": "old", "game_time": "Day 1 08:00", "count": 2},
            "last": {"snapshot_id": "head", "game_time": "Day 13 08:00", "count": 1},
        })
        self.assertEqual(rows["base"]["property"]["snapshot_id"], "rollback")
        self.assertTrue(rows["discovered"]["discovered"])
        self.assertFalse(rows["first"]["discovered"])
        self.assertNotIn("discovered", rows["near"])
        rebuilt = build_registry(self.manifest, _world(reversed(ids)), head, index, reports.get,
                                 discovery={"first": False, "discovered": True})
        self.assertEqual(registry_bytes(registry), registry_bytes(rebuilt))

    def test_incomplete_index_is_reported_and_corrupt_chains_are_rejected(self) -> None:
        manifest = {**self.manifest, "parent_snapshot_id": "ancestor"}
        world = _world(["town"])
        head = _report("head", [("town", "in_location")])
        for index in (None, {"snapshots": {"unrelated": {"parent_snapshot_id": None}}}):
            with self.subTest(index=index):
                registry = build_registry(manifest, world, head, index, lambda _: None)
                self.assertEqual(registry["coverage"], {"chain_snapshots": 1, "location_snapshots": 1,
                                                       "chain_incomplete": True})
                validate_registry(registry, manifest, world, index_data=index)
        for parent in ("missing", "head"):
            with self.subTest(parent=parent):
                index = {"snapshots": {"head": {"parent_snapshot_id": "ancestor"},
                                       "ancestor": {"parent_snapshot_id": parent}}}
                with self.assertRaisesRegex(ValueError, "оборвана или содержит цикл"):
                    build_registry(manifest, world, head, index, lambda _: None)

    def test_registry_rejects_evidence_outside_chain_or_in_reverse_order(self) -> None:
        world = _world(["town"])
        entry = {"game_time": {"day": 13, "time": "08:00"}}
        index = {"snapshots": {"head": {**entry, "parent_snapshot_id": "ancestor"},
                               "ancestor": {**entry, "parent_snapshot_id": None},
                               "side": {**entry, "parent_snapshot_id": "ancestor"}}}
        registry = build_registry(self.manifest, world, _report("head", [("town", "in_location")]),
                                  index, lambda sid: _report(sid, [("town", "in_location")]))
        validate_registry(registry, self.manifest, world, index_data=index)
        for point, updates, with_index, error in [
            ("first", {"snapshot_id": "side"}, True, "вне цепочки"),
            ("last", {"game_time": "Day 13 08:01"}, False, "позже выпуска"),
            ("first", {"game_time": "Day 13 07:59"}, True, "расходится с её временем"),
            ("last", {"game_time": "Day 13 07:59"}, False, "противоречат"),
        ]:
            with self.subTest(point=point, updates=updates):
                changed = deepcopy(registry)
                changed["locations"][0]["presence"][point].update(updates)
                with self.assertRaisesRegex(ValueError, error):
                    validate_registry(changed, self.manifest, world, index_data=index if with_index else None)
        changed = deepcopy(registry)
        presence = changed["locations"][0]["presence"]
        presence["first"], presence["last"] = presence["last"], presence["first"]
        with self.assertRaisesRegex(ValueError, "противоречат"):
            validate_registry(changed, self.manifest, world, index_data=index)

    def test_progress_transitions_preserve_orphans_and_reject_invalid_changes(self) -> None:
        world = _world(["town"])
        historical = _mark("orphan", "visited")
        previous = _progress([_mark("town", "explored"), historical], world_ref_id="world-old")
        cases = [
            ("сохранённый ID", previous, None),
            ("новый неизвестный ID", _progress([*previous["locations"], _mark("bogus", "visited")], "world-old"),
             "Неизвестный instance_id"),
            ("отметка понижена", _progress([_mark("town", "visited"), historical], "world-old"), "удалена или понижена"),
            ("отметка удалена", _progress([historical], "world-old"), "удалена или понижена"),
            ("повтор в ветке", _progress([*previous["locations"], _mark("town", "explored")], "world-old"), "Повтор отметки"),
            ("неизвестный ID в том же мире", _progress(previous["locations"]), "Неизвестный instance_id"),
        ]
        for label, progress, error in cases:
            with self.subTest(label):
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        validate_progress(progress, "campaign", world, previous)
                else:
                    self.assertEqual(validate_progress(progress, "campaign", world, previous), ["orphan"])

    def test_merge_recomputes_from_auto_and_applies_only_branch_and_time(self) -> None:
        world = _world(["visited", "tie", "other", "future", "historic"])
        report = _report("head", [("visited", "in_location"), ("tie", "in_location")])
        registry = build_registry(self.manifest, world, report, None, lambda _: None)
        progress = _progress([
            _mark("visited", "explored"), _mark("tie", "visited"),
            _mark("other", "explored", branch="other"),
            _mark("future", "visited", game_time="Day 13 08:01"),
            _mark("historic", "visited", game_time="Day 13 08:00"),
        ])
        validate_progress(progress, "campaign", world)
        merged = merge_progress(registry, progress)
        validate_registry(merged, self.manifest, world)
        self.assertEqual({row["instance_id"]: (row["status"], row["status_source"]) for row in merged["locations"]}, {
            "visited": ("explored", "manual"), "tie": ("visited", "auto"),
            "other": ("no_evidence", "none"), "future": ("no_evidence", "none"), "historic": ("visited", "manual"),
        })
        self.assertEqual(merge_progress(merged, None), registry)

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
