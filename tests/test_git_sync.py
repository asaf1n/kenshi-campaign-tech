"""Режимы отказа доставки в игровой репозиторий на временных git-репозиториях."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.deliver_companion import deliver_to_companion
from tools.gdrive_sync import ensure_buffer, ingest_inbox
from tools.io_utils import canonical, read_json
from tools.location_registry import PROGRESS_RELATIVE, REGISTRY_FILENAME, build_registry


ROOT = Path(__file__).resolve().parents[1]


class GitSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.remote = self.tmp / "remote.git"
        self.clone = self.tmp / "clone"
        self.other = self.tmp / "other"
        self.git("init", "--bare", "-b", "main", str(self.remote), cwd=self.tmp)
        self.clone.mkdir()
        self.git("init", "-b", "main", cwd=self.clone)
        self._identity(self.clone)
        (self.clone / "Campaign/Кампания").mkdir(parents=True)
        (self.clone / "Campaign/Кампания/Арка.md").write_text("канон\n", encoding="utf-8")
        (self.clone / "published").mkdir()
        (self.clone / "published/brief.md").write_text("старый\n", encoding="utf-8")
        self.git("add", ".", cwd=self.clone)
        self.git("commit", "-m", "init", cwd=self.clone)
        self.git("remote", "add", "origin", str(self.remote), cwd=self.clone)
        self.git("push", "-u", "origin", "main", cwd=self.clone)
        self.git("clone", str(self.remote), str(self.other), cwd=self.tmp)
        self._identity(self.other)

        self.published = self.tmp / "published"
        self.published.mkdir()
        (self.published / "manifest.json").write_text(json.dumps({"validation": {"status": "passed"}}), encoding="utf-8")
        (self.published / "brief.md").write_text("новый\n", encoding="utf-8")

        self.drive = self.tmp / "drive"
        ensure_buffer(self.drive)

    def git(self, *args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout

    def _identity(self, repo: Path) -> None:
        self.git("config", "user.name", "test", cwd=repo)
        self.git("config", "user.email", "test@example.invalid", cwd=repo)

    def _remote_commit(self, relative: str, text: str) -> None:
        (self.other / relative).write_text(text, encoding="utf-8")
        self.git("add", ".", cwd=self.other)
        self.git("commit", "-m", "remote", cwd=self.other)
        self.git("push", "origin", "main", cwd=self.other)

    def _deliver(self) -> dict:
        return deliver_to_companion(self.published, self.clone, auto_commit=True, auto_push=True)

    def _deliver_with_race(self, racing_text: str) -> dict:
        """Доставка, в которой другая машина успевает запушить между подготовкой клона и push."""
        import tools.deliver_companion as module

        original = module.prepare_clone

        def prepare_then_race(repo: Path) -> None:
            original(repo)
            self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
            self._remote_commit("published/brief.md", racing_text)

        with mock.patch.object(module, "prepare_clone", prepare_then_race):
            return self._deliver()

    def test_delivery_fast_forwards_before_writing_and_never_conflicts(self) -> None:
        """Ловит инцидент: коммит на устаревшей базе и rebase с конфликтом add/add в перезаписываемом файле."""
        self._remote_commit("Campaign/Кампания/Арка.md", "правка агента\n")
        self.assertEqual(self._deliver()["status"], "delivered")
        self.assertIn("новый", self.git("show", "main:published/brief.md", cwd=self.remote))

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        self._remote_commit("published/brief.md", "вторая машина\n")
        (self.published / "brief.md").write_text("третий\n", encoding="utf-8")
        result = self._deliver()
        self.assertEqual((result["status"], result["pushed"]), ("delivered", True))
        self.assertEqual(self.git("show", "main:published/brief.md", cwd=self.remote), "третий\n")
        self.assertEqual(self.git("rev-list", "--count", "origin/main~1..origin/main", cwd=self.clone).strip(), "1")
        self.assertEqual(self.git("log", "-2", "--format=%s", cwd=self.remote).splitlines()[1], "remote")

        # Коммит уже сделан: повторная доставка без изменений всё равно обязана попытаться отправить его.
        self.git("reset", "--hard", "origin/main", cwd=self.clone)
        self.git("commit", "--allow-empty", "-m", "неотправленный", cwd=self.clone)
        self.git("pull", "--rebase", "origin", "main", cwd=self.clone)
        (self.published / "brief.md").write_text("вторая машина\n", encoding="utf-8")
        self.assertTrue(self._deliver()["pushed"])
        self.assertIn("неотправленный", self.git("log", "--format=%s", "main", cwd=self.remote))

    def test_registry_delivery_validates_fresh_progress_before_writing(self) -> None:
        """Ловит слияние до актуализации клона, понижение отметки и устаревший реестр после доставки старого выпуска."""
        world = read_json((ROOT / "examples/world-reference.example.json").read_bytes())
        world_ref_id = world["world_ref_id"]
        instance = world["locations"][0]["instance_id"]
        manifest = {
            "snapshot_id": "snap-progress", "branch_id": "main", "campaign_id": "hot-lon",
            "world_ref_id": world_ref_id, "game_time": "Day 135 10:49", "validation": {"status": "passed"},
            "artifacts": {"world_reference": "world_reference.json", "location_registry": REGISTRY_FILENAME},
        }
        locations = {"snapshot_id": "snap-progress", "characters": [], "buildings": []}
        registry = build_registry(manifest, world, locations, None, lambda _: None)
        source_registry = self.published / REGISTRY_FILENAME
        source_registry.write_bytes(canonical(registry))
        (self.published / "manifest.json").write_bytes(canonical(manifest))
        (self.published / "world_reference.json").write_bytes(canonical(world))
        (self.other / PROGRESS_RELATIVE).parent.mkdir(parents=True)
        progress = {
            "format": "kenshi-location-progress/1", "campaign_id": "hot-lon", "world_ref_id": world_ref_id,
            "locations": [{"branch_id": "main", "instance_id": instance, "status": "explored", "evidence": "S-001"}],
        }
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))

        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("delivered", "ok"))
        self.assertIsNone(result["location_progress_reason"])
        target_registry = self.clone / "published" / REGISTRY_FILENAME
        before = target_registry.read_bytes()
        row = next(row for row in read_json(before)["locations"] if row["instance_id"] == instance)
        self.assertEqual((row["auto_status"], row["status"], row["status_source"]), ("no_evidence", "explored", "manual"))
        self.assertEqual(source_registry.read_bytes(), canonical(registry))
        self.assertFalse(self._deliver()["committed"])

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["locations"][0]["status"] = "visited"
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        progress["locations"][0]["evidence"] = "Уточнение сессии S-001"
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"], result["pushed"]), ("delivered", "invalid", True))
        self.assertIn("понижена", result["location_progress_reason"])
        self.assertEqual(target_registry.read_bytes(), canonical(registry))
        self.assertEqual(self.git("show", f"main:published/{REGISTRY_FILENAME}", cwd=self.remote),
                         canonical(registry).decode("utf-8"))

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["locations"][0]["status"] = "explored"
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        self.assertEqual(self._deliver()["status"], "delivered")
        self.assertEqual(read_json(target_registry.read_bytes())["locations"][0]["status"], "explored")

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["world_ref_id"] = "sha256-" + "f" * 64
        progress["locations"].append({"branch_id": "main", "instance_id": "invented-id",
                                      "status": "visited", "evidence": "S-002"})
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        progress["locations"][0]["evidence"] = "Исправление ссылки S-001"
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("delivered", "invalid"))
        self.assertIn("Неизвестный instance_id", result["location_progress_reason"])

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), "{")
        (self.published / "brief.md").write_text("выпуск с невалидными отметками\n", encoding="utf-8")
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("delivered", "invalid"))
        self.assertTrue(result["location_progress_reason"])
        self.assertIn("выпуск с невалидными отметками", self.git("show", "main:published/brief.md", cwd=self.remote))

        (self.published / "brief.md").write_text("сбой записи\n", encoding="utf-8")
        with mock.patch("tools.deliver_companion.shutil.copy2", side_effect=PermissionError("запись запрещена")):
            result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"], result["pushed"]), ("failed", "invalid", False))
        self.assertIn("запись запрещена", result["delivery_error"])
        self.assertNotIn("сбой записи", self.git("show", "main:published/brief.md", cwd=self.remote))

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["locations"].pop()
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("delivered", "ok"))
        self.assertEqual(read_json(target_registry.read_bytes())["locations"][0]["status"], "explored")

        del manifest["artifacts"]["location_registry"]
        (self.published / "manifest.json").write_bytes(canonical(manifest))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("delivered", "absent"))
        self.assertFalse(target_registry.exists())

    def test_dirty_clone_refuses_without_changes(self) -> None:
        """Ловит запись выгрузки поверх незакоммиченной правки клона."""
        (self.clone / "Campaign/Кампания/Арка.md").write_text("черновик\n", encoding="utf-8")
        head = self.git("rev-parse", "HEAD", cwd=self.clone)
        result = self._deliver()
        self.assertEqual((result["status"], result["committed"], result["pushed"]), ("failed", False, False))
        self.assertIn("незакоммиченные", result["git_error"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.clone), head)
        self.assertEqual((self.clone / "published/brief.md").read_text(encoding="utf-8"), "старый\n")

    def test_push_race_recreates_delivery_once(self) -> None:
        """Ловит потерю доставки или отказ при гонке push: коммит пересоздаётся поверх нового origin/main."""
        result = self._deliver_with_race("гонка\n")
        self.assertEqual((result["status"], result["pushed"]), ("delivered", True))
        self.assertEqual(self.git("show", "main:published/brief.md", cwd=self.remote), "новый\n")
        self.assertEqual(self.git("log", "-2", "--format=%s", cwd=self.remote).splitlines()[1], "remote")

    def test_unresolvable_race_aborts_rebase_and_keeps_clone_clean(self) -> None:
        """Ловит клон в незавершённом rebase после конфликта при гонке; выгрузка остаётся в коммите клона."""
        (self.clone / "published/brief.md").write_text("локальный\n", encoding="utf-8")
        self.git("commit", "-am", "локальный", cwd=self.clone)
        result = self._deliver_with_race("гонка\n")
        self.assertEqual((result["status"], result["pushed"]), ("failed", False))
        self.assertIn("rebase отменён", result["git_error"])
        self.assertFalse((self.clone / ".git/rebase-merge").exists())
        self.assertFalse((self.clone / ".git/rebase-apply").exists())
        self.assertEqual(self.git("status", "--porcelain", cwd=self.clone), "")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=self.clone).strip(), "main")
        self.assertEqual((self.clone / "published/brief.md").read_text(encoding="utf-8"), "новый\n")

    def test_pending_rebase_is_refused_and_left_untouched(self) -> None:
        """Ловит доставку поверх чужого незавершённого rebase и его принудительную отмену."""
        (self.clone / ".git/rebase-merge").mkdir()
        result = self._deliver()
        self.assertEqual(result["status"], "failed")
        self.assertIn("--abort", result["git_error"])
        self.assertTrue((self.clone / ".git/rebase-merge").is_dir())

    def test_ingest_requires_main_and_recovers_after_remote_moved(self) -> None:
        """Ловит перенос в processed без попадания в main и зависание повтора после чужого коммита."""
        packet = self.drive / "tmp/inbox/p.md"
        packet.write_text("=== ФАЙЛ: Новый.md ===\nа\n=== КОНЕЦ ===\n", encoding="utf-8")
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)

        self.git("checkout", "-b", "feature", cwd=self.clone)
        report = ingest_inbox(self.drive, self.clone, now=now, sync=False)
        self.assertIn("error", report)
        self.assertTrue(packet.is_file())
        self.git("checkout", "main", cwd=self.clone)

        self.git("remote", "set-url", "--push", "origin", str(self.tmp / "absent.git"), cwd=self.clone)
        self.assertEqual(len(ingest_inbox(self.drive, self.clone, now=now, sync=False)["rejected"]), 1)
        self.assertTrue(packet.is_file())

        self._remote_commit("Campaign/Кампания/Другой.md", "чужой\n")
        self.git("remote", "set-url", "--push", "origin", str(self.remote), cwd=self.clone)
        self.assertEqual(len(ingest_inbox(self.drive, self.clone, now=now, sync=False)["processed"]), 1)
        self.assertFalse(packet.exists())
        self.assertEqual(self.git("show", "main:Campaign/Кампания/Новый.md", cwd=self.remote), "а\n")


if __name__ == "__main__":
    unittest.main()
