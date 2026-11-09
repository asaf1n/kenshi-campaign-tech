"""Линия кампании (kenshi-index/2): родитель — голова кампании, перезагрузка по игровому времени, миграция индекса."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any
import unittest

from jsonschema import ValidationError
from tools.diff import diff_snapshots
from tools.index import (
    CampaignIndexError,
    campaign_chain,
    load_index,
    migrate_index,
    rebuild_index,
    register_snapshot,
    reload_of,
)
from tools.io_utils import canonical, read_json
from tools.locator import resolve_locations
from tools.publisher import publish
from tools.validation import ROOT, validate_index
from tools.world_reference import load_world_reference

try:
    SAMPLE_WREF = load_world_reference("sha256-798a47da89d9ac378677dc5a6e0b23714a68d83ea26e774c8825bdfa8b0f20ec")
except Exception:
    SAMPLE_WREF = read_json((ROOT / "examples/world-reference.example.json").read_bytes())


def _source_sha(snapshot_id: str) -> str:
    return hashlib.sha256(f"source-{snapshot_id}".encode()).hexdigest()


def _create_snapshot_dir(snapshots_dir: Path, snapshot_id: str, day: int, time_str: str) -> Path:
    sdir = snapshots_dir / snapshot_id
    sdir.mkdir(parents=True, exist_ok=True)
    character = {
        "id": "ps:1", "name": "Хорек", "squad_id": "sq_1", "squad_name": "Беглецы", "race": "Greenlander",
        "status": {"dead": False, "unconscious": False},
        "attributes_base": {"Strength": 20.0 + day}, "skills_base": {"Athletics": 30.0 + day},
        "inventory": [{"name": "Аптечка", "sid": "1359-gamedata.base", "quantity": 3}],
        "coordinates": [-1000.0 + day, 50.0, -2000.0],
    }
    current = {
        "format": "kenshi-session/10", "save": {}, "world": {"day": day, "time": time_str, "money": 5000 + day * 100},
        "characters": [character], "squads": [{"squad_id": "sq_1", "squad_name": "Беглецы", "members": ["Хорек"]}],
        "supplies": {"medkits": {"total": 3, "nutrition": 0}}, "property": {"buildings": []}, "warnings": [], "meta": {},
    }
    (sdir / "current.json").write_bytes(canonical(current))
    provenance = {
        "format": "kenshi-snapshot/2", "snapshot_id": snapshot_id, "source_kind": "save",
        "source_format": "kenshi-session/10", "source_sha256": _source_sha(snapshot_id),
        "game_time": {"day": day, "time": time_str},
    }
    (sdir / "provenance.json").write_bytes(canonical(provenance))
    return sdir


def _sid(char: str) -> str:
    return "sha256-" + char * 64


class CampaignLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="kenshi-test-line-"))
        self.snapshots_dir = self.root / "snapshots"
        self.diffs_dir = self.root / "diffs"
        self.published_dir = self.root / "published"
        self.index_file = self.root / "index.json"
        for directory in (self.snapshots_dir, self.diffs_dir, self.published_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.published_dir / ".gitkeep").write_bytes(b"")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def register(self, sid: str, day: int, time_str: str, campaign: str = "camp") -> dict[str, Any]:
        _create_snapshot_dir(self.snapshots_dir, sid, day=day, time_str=time_str)
        return register_snapshot(sid, campaign, game_time={"day": day, "time": time_str},
                                 world_ref_id=SAMPLE_WREF["world_ref_id"], source_sha256=_source_sha(sid),
                                 index_path=self.index_file)

    def publish(self, sid: str) -> dict[str, Any]:
        publish(snapshot_id=sid, snapshots_dir=self.snapshots_dir, diffs_dir=self.diffs_dir,
                published_dir=self.published_dir, world_ref_id=SAMPLE_WREF["world_ref_id"], index_path=self.index_file)
        return read_json((self.published_dir / "manifest.json").read_bytes())

    def test_schema_example_and_integrity(self) -> None:
        """Пример kenshi-index/2 валиден; цикл, сирота, родитель чужой кампании и поля веток отклоняются."""
        example = read_json((ROOT / "examples/index.example.json").read_bytes())
        self.assertEqual(validate_index(example)["status"], "passed")
        ids = sorted(example["snapshots"])
        for label, mutate in (
            ("цикл", lambda d: d["snapshots"][ids[0]].update(parent_snapshot_id=ids[-1])),
            ("сирота", lambda d: d["snapshots"][ids[0]].update(parent_snapshot_id="missing")),
            ("чужая кампания", lambda d: d["snapshots"][ids[1]].update(campaign_id="other")),
            ("ветки в корне", lambda d: d.update(branches={})),
            ("ветка в записи", lambda d: d["snapshots"][ids[0]].update(branch_id="main")),
        ):
            with self.subTest(label):
                broken = copy.deepcopy(example)
                mutate(broken)
                with self.assertRaises((ValueError, ValidationError)):
                    validate_index(broken)

    def test_parent_is_campaign_head_and_registration_is_idempotent(self) -> None:
        a, b, c = _sid("a"), _sid("b"), _sid("c")
        self.assertIsNone(self.register(a, 10, "10:00")["parent_snapshot_id"])
        self.assertEqual(self.register(b, 10, "14:00")["parent_snapshot_id"], a)
        before = self.index_file.read_bytes()
        self.assertEqual(self.register(b, 10, "14:00")["parent_snapshot_id"], a)
        self.assertEqual(self.index_file.read_bytes(), before)  # повтор головы ничего не меняет

        # Повтор известного снимка не меняет записи и делает его головой: следующий снимок продолжает от него.
        self.register(a, 10, "10:00")
        index = load_index(self.index_file)
        self.assertEqual(index["campaigns"]["camp"]["head_snapshot_id"], a)
        self.assertEqual(index["snapshots"][b]["parent_snapshot_id"], a)
        self.assertEqual(self.register(c, 10, "12:00")["parent_snapshot_id"], a)

        # Другая кампания ведёт свою линию; снимок одной кампании в другую не регистрируется.
        self.assertIsNone(self.register(_sid("d"), 1, "00:00", campaign="other")["parent_snapshot_id"])
        with self.assertRaises(CampaignIndexError):
            self.register(a, 10, "10:00", campaign="other")

    def test_reload_excludes_cancelled_snapshots_and_publishes_without_diff(self) -> None:
        """A 10:00 -> B 14:00 -> R 12:00 (перезагрузка): B отменён, diff B->R невалиден, публикация R успешна."""
        a, b, r = _sid("a"), _sid("b"), _sid("e")
        self.register(a, 10, "10:00")
        self.register(b, 10, "14:00")
        self.register(r, 10, "12:00")
        index = load_index(self.index_file)
        self.assertEqual(index["snapshots"][r]["parent_snapshot_id"], b)
        self.assertEqual(campaign_chain(index, r), [a, r])
        self.assertEqual(campaign_chain(index, b), [a, b])
        self.assertEqual(reload_of(index, r), {"from_snapshot": b, "from_game_time": {"day": 10, "time": "14:00"}})
        self.assertIsNone(reload_of(index, b))

        for sid in (a, b):
            self.assertNotIn("reload", self.publish(sid))
        manifest = self.publish(r)
        self.assertEqual(manifest["reload"], {"from_snapshot": b, "from_game_time": "Day 10 14:00"})
        self.assertEqual(manifest["parent_snapshot_id"], b)
        self.assertNotIn("branch_id", manifest)
        changes = read_json((self.published_dir / "changes.json").read_bytes())
        self.assertEqual((changes["valid"], changes["reason"]), (False, "game_time_regression"))
        self.assertEqual(changes["roster"], {})
        events = [read_json(line.encode()) for line in
                  (self.published_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["kind"] for e in events if e["kind"] == "reload"], ["reload"])
        self.assertTrue(all(e["snapshot_id"] != b for e in events))  # события отменённого снимка исключены
        registry = read_json((self.published_dir / "location_registry.json").read_bytes())
        self.assertEqual(registry["coverage"]["chain_snapshots"], 2)

        # A — предок R в цепочке: diff A->R валиден. Боковой лист вне цепочки с R не сравнивается.
        self.assertTrue(diff_snapshots(self.snapshots_dir / a, self.snapshots_dir / r,
                                       index_path=self.index_file)["valid"])
        self.register(a, 10, "10:00")
        side = _sid("f")
        self.register(side, 10, "16:00")
        diff_side = diff_snapshots(self.snapshots_dir / r, self.snapshots_dir / side, index_path=self.index_file)
        self.assertEqual((diff_side["valid"], diff_side["reason"]), (False, "not_in_chain"))
        loc = resolve_locations(current=read_json((self.snapshots_dir / side / "current.json").read_bytes()),
                                world_ref=SAMPLE_WREF, snapshot_id=side,
                                previous_locations=read_json((self.published_dir / "locations.json").read_bytes()),
                                changes=diff_side)
        self.assertFalse(loc["movement"]["valid"])
        self.assertEqual(loc["movement"]["characters"], [])

    def test_migrate_legacy_index_with_backup(self) -> None:
        """kenshi-index/1 с двумя ветками: план, применение с резервной копией, повтор без изменений."""
        def row(ch: str, parent: str | None, branch: str, campaign: str | None, day: int, at: int) -> dict[str, Any]:
            return {"snapshot_id": _sid(ch), "parent_snapshot_id": parent and _sid(parent), "branch_id": branch,
                    "campaign_id": campaign, "game_time": {"day": day, "time": "10:00"}, "world_ref_id": None,
                    "source_kind": "save", "source_sha256": ch * 64, "registered_at": f"2026-11-0{at}T00:00:00Z"}

        rows = [row("a", None, "line", "hot", 1, 1), row("b", "a", "line", "hot", 2, 2),
                row("c", None, "main", "hot", 1, 3), row("d", "b", "line", "hot", 3, 4),
                row("e", None, "main", "e2e", 1, 5)]
        branch = lambda head: {"name": "x", "head_snapshot_id": _sid(head), "created_at": "2026-11-01T00:00:00Z"}  # noqa: E731
        legacy = {"format": "kenshi-index/1", "updated_at": "2026-11-05T00:00:00Z", "active_branch_id": "main",
                  "branches": {"line": branch("d"), "main": branch("e")},
                  "snapshots": {r["snapshot_id"]: r for r in rows}}
        raw = canonical(legacy)
        self.index_file.write_bytes(raw)
        with self.assertRaisesRegex(CampaignIndexError, "index migrate"):
            load_index(self.index_file)

        plan = migrate_index(self.index_file)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(self.index_file.read_bytes(), raw)
        done = migrate_index(self.index_file, apply=True)
        self.assertEqual(done["status"], "migrated")
        self.assertEqual((self.root / done["backup"]).read_bytes(), raw)
        migrated = load_index(self.index_file)
        self.assertEqual(migrated["campaigns"], {"hot": {"head_snapshot_id": _sid("d")},
                                                 "e2e": {"head_snapshot_id": _sid("e")}})
        self.assertEqual(plan["campaigns"]["hot"]["head_snapshot_id"], _sid("d"))
        self.assertEqual({sid: e["parent_snapshot_id"] for sid, e in migrated["snapshots"].items()},
                         {r["snapshot_id"]: r["parent_snapshot_id"] for r in rows})
        self.assertTrue(all("branch_id" not in e for e in migrated["snapshots"].values()))
        self.assertEqual(migrate_index(self.index_file, apply=True)["status"], "unchanged")

        legacy["snapshots"][_sid("a")]["campaign_id"] = None
        self.index_file.write_bytes(canonical(legacy))
        with self.assertRaises(CampaignIndexError):
            migrate_index(self.index_file, apply=True)

    def test_rebuild_keeps_index_links_and_cli(self) -> None:
        from tools.campaign import main as campaign_main

        a, b, orphan = _sid("1"), _sid("2"), _sid("3")
        self.register(a, 5, "08:00")
        self.register(b, 6, "12:00")
        _create_snapshot_dir(self.snapshots_dir, orphan, day=7, time_str="09:00")
        rebuilt = rebuild_index(self.snapshots_dir, index_path=self.index_file)
        self.assertEqual(rebuilt["snapshots"][b]["parent_snapshot_id"], a)
        self.assertEqual(rebuilt["campaigns"], {"camp": {"head_snapshot_id": b}})
        self.assertEqual(rebuilt["skipped_unindexed"], [orphan])
        for argv in (["index", "validate"], ["index", "show"], ["index", "migrate"]):
            with self.subTest(argv=argv):
                self.assertEqual(campaign_main([*argv, "--index-file", str(self.index_file)]), 0)


if __name__ == "__main__":
    unittest.main()
