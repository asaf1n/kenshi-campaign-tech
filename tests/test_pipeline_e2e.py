"""Сквозной интеграционный тест вертикального среза: extract -> diff -> validate -> render -> publish."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.config import Config
from tools.io_utils import file_hash, read_json
from tools.pipeline import run_pipeline
from tools.validation import validate_publication

ROOT = Path(__file__).resolve().parents[1]
REAL_EXPORT = Path("C:/Users/artem/kenshi-platoon-export/kenshi-session.json")


class PipelineE2ETests(unittest.TestCase):
    def test_end_to_end_pipeline_on_real_export(self) -> None:
        if not REAL_EXPORT.is_file():
            self.skipTest(f"Реальный экспорт не найден: {REAL_EXPORT}")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            snapshots_dir = tmp / "snapshots"
            diffs_dir = tmp / "diffs"
            published_dir = tmp / "published"
            published_dir.mkdir(parents=True)
            (published_dir / ".gitkeep").write_bytes(b"")

            config = Config.from_env()

            # Первый снимок (baseline = None)
            res1 = run_pipeline(
                source=REAL_EXPORT,
                source_kind="export",
                config=config,
                output_snapshots=snapshots_dir,
                output_diffs=diffs_dir,
                published_dir=published_dir,
                campaign_id="e2e-test",
                branch_id="main",
            )

            self.assertEqual(res1["pipeline_status"], "success")
            snap1_id = res1["snapshot_id"]
            self.assertTrue((published_dir / "manifest.json").is_file())
            self.assertTrue((published_dir / "current.json").is_file())
            self.assertTrue((published_dir / "changes.json").is_file())
            self.assertTrue((published_dir / "brief.md").is_file())
            self.assertTrue((published_dir / "squad.md").is_file())
            self.assertTrue((published_dir / ".gitkeep").is_file())

            # Валидация опубликованного пакета
            val1 = validate_publication(published_dir)
            self.assertEqual(val1["status"], "passed")

            # Второй запуск (с указанием parent_snapshot_id = snap1_id)
            # В данном тесте тот же источник выступает дочерним снимком с нулевыми дельтами
            res2 = run_pipeline(
                source=REAL_EXPORT,
                source_kind="export",
                config=config,
                output_snapshots=snapshots_dir,
                output_diffs=diffs_dir,
                published_dir=published_dir,
                campaign_id="e2e-test",
                branch_id="main",
                parent_snapshot_id=snap1_id,
            )
            self.assertEqual(res2["pipeline_status"], "success")
            val2 = validate_publication(published_dir)
            self.assertEqual(val2["status"], "passed")

            manifest2 = read_json((published_dir / "manifest.json").read_bytes())
            self.assertEqual(manifest2["parent_snapshot_id"], snap1_id)

            changes2 = read_json((published_dir / "changes.json").read_bytes())
            self.assertTrue(changes2["valid"])
            self.assertEqual(changes2["from"]["snapshot_id"], snap1_id)
            self.assertEqual(changes2["to"]["snapshot_id"], res2["snapshot_id"])
            # Число сравненных сущностей ненулевое
            self.assertTrue(sum(changes2["compared"].values()) > 0)

    def test_cli_pipeline_subcommand(self) -> None:
        if not REAL_EXPORT.is_file():
            self.skipTest(f"Реальный экспорт не найден: {REAL_EXPORT}")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            snapshots_dir = tmp / "snapshots"
            diffs_dir = tmp / "diffs"
            published_dir = tmp / "published"
            published_dir.mkdir(parents=True)
            (published_dir / ".gitkeep").write_bytes(b"")

            cmd = [
                sys.executable,
                "-m",
                "tools.campaign",
                "pipeline",
                "--source-kind",
                "export",
                "--source",
                str(REAL_EXPORT),
                "--output",
                str(snapshots_dir),
                "--diffs-dir",
                str(diffs_dir),
                "--published-dir",
                str(published_dir),
                "--campaign-id",
                "cli-test",
                "--branch-id",
                "main",
            ]
            completed = subprocess.run(cmd, cwd=ROOT, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))

            val = validate_publication(published_dir)
            self.assertEqual(val["status"], "passed")
            self.assertTrue((published_dir / ".gitkeep").is_file())

    def test_cli_pipeline_deliver_to(self) -> None:
        """--deliver-to доставляет published/ только после реальной публикации; при --dry-run пропускается."""
        if not REAL_EXPORT.is_file():
            self.skipTest(f"Реальный экспорт не найден: {REAL_EXPORT}")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            companion = tmp / "companion"
            companion.mkdir()
            published_dir = tmp / "published"
            published_dir.mkdir()
            base = [
                sys.executable, "-m", "tools.campaign", "pipeline",
                "--source-kind", "export", "--source", str(REAL_EXPORT),
                "--output", str(tmp / "snapshots"), "--diffs-dir", str(tmp / "diffs"),
                "--published-dir", str(published_dir),
                "--campaign-id", "cli-test", "--branch-id", "main",
                "--deliver-to", str(companion),
            ]
            dry = subprocess.run([*base, "--dry-run"], cwd=ROOT, capture_output=True)
            self.assertEqual(dry.returncode, 0, dry.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(json.loads(dry.stdout)["companion_delivery"]["status"], "skipped")
            self.assertFalse((companion / "published").exists())

            real = subprocess.run(base, cwd=ROOT, capture_output=True)
            self.assertEqual(real.returncode, 0, real.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(json.loads(real.stdout)["companion_delivery"]["status"], "delivered")
            self.assertTrue((companion / "published" / "manifest.json").is_file())
