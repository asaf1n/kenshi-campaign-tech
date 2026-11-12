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
from tools.gdrive_sync import ensure_buffer
from tools.io_utils import canonical, read_json
from tools.location_registry import PROGRESS_RELATIVE, REGISTRY_FILENAME, build_registry
from tools.validation import validate_unique_entities_registry


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
        """Ловит слияние до актуализации клона, удаление отметки зачистки (база в истории — формат /1 с branch_id)
        и устаревший реестр после доставки старого выпуска."""
        world = read_json((ROOT / "examples/world-reference.example.json").read_bytes())
        world_ref_id = world["world_ref_id"]
        instance = world["locations"][0]["instance_id"]
        manifest = {
            "snapshot_id": "snap-progress", "campaign_id": "hot-lon",
            "world_ref_id": world_ref_id, "game_time": "Day 135 10:49", "validation": {"status": "passed"},
            "artifacts": {"world_reference": "world_reference.json", "location_registry": REGISTRY_FILENAME},
        }
        registry = build_registry(manifest, world)
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
        self.assertEqual(row["cleared"], {"evidence": "S-001"})
        self.assertEqual(source_registry.read_bytes(), canonical(registry))
        self.assertFalse(self._deliver()["committed"])

        # Миграция на /3: база «не удалено» остаётся в версии /1 из истории Git; снятая отметка зачистки — ошибка.
        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        cleared = {"instance_id": instance, "evidence": "S-001"}
        progress = {"format": "kenshi-location-progress/3", "campaign_id": "hot-lon", "world_ref_id": world_ref_id,
                    "locations": []}
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"], result["pushed"]), ("degraded", "invalid", True))
        self.assertIn("удалена", result["location_progress_reason"])
        self.assertEqual(read_json(target_registry.read_bytes())["locations"][0]["cleared"], {"evidence": "S-001"})
        self.assertEqual(read_json(self.git("show", f"main:published/{REGISTRY_FILENAME}", cwd=self.remote).encode("utf-8"))["locations"][0]["cleared"],
                         {"evidence": "S-001"})

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["locations"] = [dict(cleared)]
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        self.assertEqual(self._deliver()["status"], "delivered")
        self.assertEqual(read_json(target_registry.read_bytes())["locations"][0]["cleared"], {"evidence": "S-001"})

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        progress["world_ref_id"] = "sha256-" + "f" * 64
        progress["locations"].append({"instance_id": "invented-id", "evidence": "S-002"})
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        progress["locations"][0]["evidence"] = "Исправление ссылки S-001"
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("degraded", "invalid"))
        self.assertIn("Неизвестный instance_id", result["location_progress_reason"])

        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), "{")
        (self.published / "brief.md").write_text("выпуск с невалидными отметками\n", encoding="utf-8")
        result = self._deliver()
        self.assertEqual((result["status"], result["location_progress"]), ("degraded", "invalid"))
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
        self.assertEqual(read_json(target_registry.read_bytes())["locations"][0]["cleared"],
                         {"evidence": "Исправление ссылки S-001"})

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

    def test_unique_entities_delivery_and_progress_enrichment(self) -> None:
        """Проверяет доставку unique_entities_registry.json и его обогащение ручным слоем прогресса."""
        world = read_json((ROOT / "examples/world-reference.example.json").read_bytes())
        world_ref_id = world["world_ref_id"]
        instance = world["locations"][0]["instance_id"]
        manifest = {
            "snapshot_id": "snap-entities", "campaign_id": "hot-lon",
            "world_ref_id": world_ref_id, "game_time": "Day 135 10:49", "validation": {"status": "passed"},
            "artifacts": {"world_reference": "world_reference.json"},
        }
        (self.published / "manifest.json").write_bytes(canonical(manifest))
        (self.published / "world_reference.json").write_bytes(canonical(world))

        entities = {
            "format": "kenshi-unique-entities-registry/2",
            "campaign_id": "hot-lon",
            "snapshot_id": "snap-entities",
            "world_ref_id": world_ref_id,
            "game_time": "Day 135 10:49",
            "generated_at": "2026-11-11T00:00:00Z",
            "unique_npcs": [],
            "bosses": [{
                "npc_sid": "boss-test",
                "name_en": "Test Boss",
                "name_ru": "Тестовый босс",
                "category": "boss",
                "classification_origin": "game_bounty",
                "bounty": None,
                "associated_world_states": [],
                "static_locations": [{
                    "instance_id": instance, "template_sid": "tpl-1", "location_name_ru": "Локация",
                    "location_name_en": "Location", "region": "border-zone",
                    "link_type": "resident", "resolution": "unique", "source_chain": ["TOWN:tpl-1"],
                    "cleared_evidence": None,
                }],
                "observed_state": {"state_code": 1, "player_defeat_flag": False, "captured_by": None},
                "cleared_evidence": None,
                "personal_evidence": {"event_id": "ev-test", "day": 135, "summary": "Победа [npc_sid=boss-test]"},
            }],
            "meitou_weapons": [],
        }
        validate_unique_entities_registry(entities)
        source_entities_path = self.published / "unique_entities_registry.json"
        source_entities_path.write_bytes(canonical(entities))

        # 1. Доставка без прогресса: файл скопирован в клон
        res = self._deliver()
        self.assertEqual(res["status"], "delivered")
        target_entities_path = self.clone / "published/unique_entities_registry.json"
        self.assertTrue(target_entities_path.is_file())
        delivered = read_json(target_entities_path.read_bytes())
        self.assertIsNone(delivered["bosses"][0]["cleared_evidence"])

        # 2. Появление прогресса в клоне: обогащение JSON и пересборка unique_entities.csv
        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        (self.other / PROGRESS_RELATIVE).parent.mkdir(parents=True, exist_ok=True)
        progress = {
            "format": "kenshi-location-progress/3", "campaign_id": "hot-lon", "world_ref_id": world_ref_id,
            "locations": [{"instance_id": instance, "evidence": "session-boss-cleared",
                           "game_time": "Day 135 10:00"}],
        }
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))

        res2 = self._deliver()
        self.assertEqual(res2["status"], "delivered")
        delivered2 = read_json(target_entities_path.read_bytes())
        validate_unique_entities_registry(delivered2)
        boss = delivered2["bosses"][0]
        self.assertIsNotNone(boss["cleared_evidence"])
        self.assertEqual(boss["cleared_evidence"]["location_instance_id"], instance)
        self.assertEqual(boss["cleared_evidence"]["source"], "session-boss-cleared")
        self.assertEqual(boss["cleared_evidence"]["cleared_at"], "Day 135 10:00")

        # Таблица unique_entities.csv пересобрана из обогащённого JSON
        target_csv_path = self.clone / "published/tables/unique_entities.csv"
        self.assertTrue(target_csv_path.is_file())
        csv_text = target_csv_path.read_text(encoding="utf-8")
        self.assertIn("boss-test", csv_text)
        self.assertIn("session-boss-cleared", csv_text)

        # 3. Защита ручного слоя при ошибке: невалидный прогресс выставляет status: degraded
        # и блокирует перезапись зависимых файлов (unique_entities_registry.json, unique_entities.csv)
        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), "{")
        res_deg = self._deliver()
        self.assertEqual((res_deg["status"], res_deg["location_progress"]), ("degraded", "invalid"))
        # Зависимые файлы не перезаписаны и сохраняют ранее валидное состояние
        self.assertEqual(target_entities_path.read_bytes(), canonical(delivered2))
        self.assertEqual(target_csv_path.read_text(encoding="utf-8"), csv_text)

        # Восстановление после исправления файла прогресса
        self.git("pull", "--ff-only", "origin", "main", cwd=self.other)
        self._remote_commit(PROGRESS_RELATIVE.as_posix(), canonical(progress).decode("utf-8"))
        res_rec = self._deliver()
        self.assertEqual((res_rec["status"], res_rec["location_progress"]), ("delivered", "ok"))

        # Исходный файл в published/ остался без изменений
        self.assertEqual(source_entities_path.read_bytes(), canonical(entities))

        # 4. Удаление реестра из published/: файл удаляется и в клоне
        source_entities_path.unlink()
        res3 = self._deliver()
        self.assertEqual(res3["status"], "delivered")
        self.assertFalse(target_entities_path.exists())



if __name__ == "__main__":
    unittest.main()
