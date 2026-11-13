"""Сохранность истории ручного слоя entities_progress и команда check-entities-progress."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.deliver_companion import ENTITIES_PROGRESS_RELATIVE, _read_entities_progress, check_entities_progress
from tools.io_utils import canonical
from tools.unique_entities_registry import BOSS_CLEARED_OUTCOMES, NPC_OUTCOMES, TROPHY_OUTCOMES


def _layer(entities: dict) -> bytes:
    return canonical({"format": "kenshi-entities-progress/1", "campaign_id": "hot-lon", "entities": entities})


class EntitiesProgressHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(holder.name) / "clone"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.path = self.repo / ENTITIES_PROGRESS_RELATIVE
        self.path.parent.mkdir(parents=True)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=str(self.repo), check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout

    def commit(self, data: bytes) -> None:
        self.path.write_bytes(data)
        self.git("add", ".")
        self.git("commit", "-m", "layer")

    def test_outcome_vocabulary(self) -> None:
        self.assertEqual(NPC_OUTCOMES, {"dead", "defeated", "captured", "handed_over", "released", "recruited"})
        self.assertEqual(TROPHY_OUTCOMES, {"trophy_claimed"})
        self.assertIn("handed_over", BOSS_CLEARED_OUTCOMES)
        self.assertNotIn("released", BOSS_CLEARED_OUTCOMES)

    def test_outcome_may_change_but_committed_key_stays(self) -> None:
        self.commit(_layer({"boss-a": {"outcome": "captured", "evidence": "S-1"}}))
        self.path.write_bytes(_layer({"boss-a": {"outcome": "handed_over", "evidence": "S-2"}}))
        progress, status, _ = _read_entities_progress(self.repo, "hot-lon")
        self.assertEqual((status, progress["entities"]["boss-a"]["outcome"]), ("ok", "handed_over"))

        self.path.write_bytes(_layer({"boss-b": {"outcome": "dead", "evidence": "S-2"}}))
        progress, status, reason = _read_entities_progress(self.repo, "hot-lon")
        self.assertIsNone(progress)
        self.assertEqual(status, "invalid")
        self.assertIn("boss-a", reason)

    def test_uncommitted_key_can_be_removed_and_deleted_file_is_invalid(self) -> None:
        self.commit(_layer({}))
        self.path.write_bytes(_layer({"boss-a": {"outcome": "dead", "evidence": "S-1"}}))
        self.assertEqual(_read_entities_progress(self.repo, "hot-lon")[1], "ok")
        self.path.write_bytes(_layer({}))
        self.assertEqual(_read_entities_progress(self.repo, "hot-lon")[1], "ok")

        self.commit(_layer({"boss-a": {"outcome": "dead", "evidence": "S-1"}}))
        self.path.unlink()
        progress, status, reason = _read_entities_progress(self.repo, "hot-lon")
        self.assertEqual((progress, status), (None, "invalid"))
        self.assertIn("удалён", reason)

    def test_invalid_historical_version_does_not_lock_keys(self) -> None:
        self.commit(b'{"format": "broken"}')
        self.commit(_layer({}))
        self.assertEqual(_read_entities_progress(self.repo, "hot-lon")[1], "ok")

    def test_check_command_reports_unknown_sids_and_outcomes(self) -> None:
        published = Path(self.repo).parent / "published"
        published.mkdir()
        (published / "manifest.json").write_text(json.dumps({"campaign_id": "hot-lon"}), encoding="utf-8")
        (published / "unique_entities_registry.json").write_bytes(canonical({
            "unique_npcs": [{"npc_sid": "npc-a"}], "bosses": [{"npc_sid": "boss-a"}],
            "meitou_weapons": [{"base_sid": "blade-a"}],
        }))
        self.commit(_layer({
            "boss-a": {"outcome": "handed_over", "evidence": "S-1"},
            "blade-a": {"outcome": "trophy_claimed", "evidence": "S-1"},
        }))
        self.assertEqual(check_entities_progress(published, self.repo)["status"], "passed")

        self.commit(_layer({
            "boss-a": {"outcome": "handed_over", "evidence": "S-1"},
            "blade-a": {"outcome": "dead", "evidence": "S-1"},
            "ghost": {"outcome": "dead", "evidence": "S-2"},
        }))
        result = check_entities_progress(published, self.repo)
        self.assertEqual(result["status"], "warnings")
        self.assertEqual(result["unknown_sids"], ["ghost"])
        self.assertEqual(result["unknown_outcomes"], ["blade-a: dead"])


if __name__ == "__main__":
    unittest.main()
