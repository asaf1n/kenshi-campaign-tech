"""Проверка принадлежности сейва кампании до публикации."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import campaign
from tools.campaign_check import CampaignMismatchError, compare_with_parent

PARENT = "sha256-" + "a" * 64
CHILD = "sha256-" + "b" * 64


def state(ids: list[str]) -> dict:
    return {"characters": [{"id": item, "name": "Одинаковое имя"} for item in ids],
            "diplomacy": {"player_faction_sid": "204-gamedata.base"}}


def write_snapshot(root: Path, snapshot_id: str, ids: list[str]) -> None:
    (root / snapshot_id).mkdir(parents=True)
    (root / snapshot_id / "current.json").write_text(json.dumps(state(ids)), encoding="utf-8")
    (root / snapshot_id / "provenance.json").write_text("{}", encoding="utf-8")


class CampaignCheckTests(unittest.TestCase):
    def run_cli(self, tmp: Path, child_ids: list[str], extra: list[str]) -> tuple[int, mock.Mock, mock.Mock, mock.Mock]:
        snapshots = tmp / "snapshots"
        write_snapshot(snapshots, PARENT, [f"ps:{n}" for n in range(10)])
        write_snapshot(snapshots, CHILD, child_ids)
        published = tmp / "published"
        published.mkdir()
        (tmp / "companion").mkdir()
        (published / "marker.txt").write_text("prev", encoding="utf-8")
        argv = ["pipeline", "--source-kind", "save", "--source", str(tmp), "--output", str(snapshots),
                "--published-dir", str(published), "--campaign-id", "hot-lon", "--branch-id", "main",
                "--parent-snapshot-id", PARENT, "--deliver-to", str(tmp / "companion"), "--sync-drive", *extra]
        with mock.patch("tools.pipeline.extract", return_value={"snapshot_id": CHILD}), \
                mock.patch("tools.pipeline.register_snapshot", return_value={}), \
                mock.patch("tools.pipeline.publish", return_value={"manifest": {"validation": {"status": "passed"}}}) as publish, \
                mock.patch("tools.deliver_companion.deliver_to_companion", return_value={"status": "delivered"}) as deliver, \
                mock.patch("tools.gdrive_sync.sync_drive", return_value={"status": "synced"}) as sync, \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            code = campaign.main(argv)
        self.assertEqual((published / "marker.txt").read_text(encoding="utf-8"), "prev")
        return code, publish, deliver, sync

    def test_foreign_save_rejected_before_publish(self) -> None:
        """Чужой состав: код 1, publish/доставка/Диск не вызываются (ловит проверку после публикации)."""
        with tempfile.TemporaryDirectory() as tmp:
            code, publish, deliver, sync = self.run_cli(Path(tmp), [f"ps:x{n}" for n in range(10)], [])
        self.assertEqual(code, 1)
        publish.assert_not_called()
        deliver.assert_not_called()
        sync.assert_not_called()

    def test_foreign_save_allowed_explicitly(self) -> None:
        """--allow-new-campaign пропускает проверку (ловит безвыходную блокировку нового старта)."""
        with tempfile.TemporaryDirectory() as tmp:
            code, publish, _, _ = self.run_cli(Path(tmp), [f"ps:x{n}" for n in range(10)], ["--allow-new-campaign"])
        self.assertEqual(code, 0)
        publish.assert_called_once()

    def test_continuation_variants_pass(self) -> None:
        """Продолжения: прежний состав, потеря/рост состава, старый сейв; одноимённые чужие персонажи не засчитываются."""
        base = [f"ps:{n}" for n in range(10)]
        cases = {
            "same": (base, True),
            "old_save_fewer": (base[:3], True),
            "grown": (base + [f"ps:new{n}" for n in range(20)], True),
            "foreign_same_names": ([f"ps:x{n}" for n in range(10)], False),
            "empty": ([], False),
        }
        for label, (ids, belongs) in cases.items():
            with self.subTest(label):
                self.assertEqual(compare_with_parent(state(base), state(ids))["belongs"], belongs)
        self.assertTrue(issubclass(CampaignMismatchError, ValueError))


if __name__ == "__main__":
    unittest.main()
