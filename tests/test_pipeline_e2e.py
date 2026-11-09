"""Сквозной интеграционный тест вертикального среза: extract -> diff -> validate -> render -> publish."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.config import Config
from tools.index import register_snapshot
from tools.io_utils import canonical, file_hash, read_json
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
            )

            self.assertEqual(res1["pipeline_status"], "success")
            snap1_id = res1["snapshot_id"]
            self.assertTrue((published_dir / "manifest.json").is_file())
            self.assertTrue((published_dir / "extended" / "current.json").is_file())
            self.assertTrue((published_dir / "changes.json").is_file())
            self.assertTrue((published_dir / "brief.md").is_file())
            self.assertTrue((published_dir / "squad.md").is_file())
            self.assertTrue((published_dir / ".gitkeep").is_file())

            # Валидация опубликованного пакета
            val1 = validate_publication(published_dir)
            self.assertEqual(val1["status"], "passed")

            # Повтор того же источника идемпотентен: тот же снимок, индекс и выпуск не меняются.
            index_bytes = (tmp / "index.json").read_bytes()
            bundle = {p.relative_to(published_dir).as_posix(): file_hash(p)
                      for p in published_dir.rglob("*") if p.is_file() and p.name != "manifest.json"}
            res_repeat = run_pipeline(source=REAL_EXPORT, source_kind="export", config=config,
                                      output_snapshots=snapshots_dir, output_diffs=diffs_dir,
                                      published_dir=published_dir, campaign_id="e2e-test")
            self.assertEqual(res_repeat["snapshot_id"], snap1_id)
            self.assertEqual((tmp / "index.json").read_bytes(), index_bytes)
            self.assertEqual(bundle, {p.relative_to(published_dir).as_posix(): file_hash(p)
                                      for p in published_dir.rglob("*") if p.is_file() and p.name != "manifest.json"})

            # Тот же источник в другой кампании с другим родителем даёт тот же snapshot_id; родитель — голова кампании.
            other_index = tmp / "other/index.json"
            parent_id = "sha256-" + "e" * 64
            parent_dir = snapshots_dir / parent_id
            parent_dir.mkdir()
            (parent_dir / "current.json").write_bytes((snapshots_dir / snap1_id / "current.json").read_bytes())
            provenance = read_json((snapshots_dir / snap1_id / "provenance.json").read_bytes())
            (parent_dir / "provenance.json").write_bytes(canonical({**provenance, "snapshot_id": parent_id}))
            register_snapshot(parent_id, "e2e-other", game_time=provenance["game_time"],
                              source_kind="export", source_sha256="f" * 64, index_path=other_index)
            other_published = tmp / "other/published"
            other_published.mkdir()
            res2 = run_pipeline(source=REAL_EXPORT, source_kind="export", config=config,
                                output_snapshots=snapshots_dir, output_diffs=diffs_dir,
                                published_dir=other_published, campaign_id="e2e-other", index_path=other_index)
            self.assertEqual(res2["pipeline_status"], "success")
            self.assertEqual(res2["snapshot_id"], snap1_id)
            self.assertEqual(validate_publication(other_published)["status"], "passed")
            manifest2 = read_json((other_published / "manifest.json").read_bytes())
            self.assertEqual(manifest2["parent_snapshot_id"], parent_id)
            changes2 = read_json((other_published / "changes.json").read_bytes())
            self.assertTrue(changes2["valid"])
            self.assertEqual(changes2["to"]["snapshot_id"], snap1_id)
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
            ]
            completed = subprocess.run(cmd, cwd=ROOT, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))

            val = validate_publication(published_dir)
            self.assertEqual(val["status"], "passed")
            self.assertTrue((published_dir / ".gitkeep").is_file())

    def test_cli_pipeline_deliver_to(self) -> None:
        """--deliver-to доставляет published/ после реальной публикации; при --dry-run published/ не меняется и не доставляется."""
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
                "--campaign-id", "cli-test",
                "--deliver-to", str(companion),
            ]
            dry = subprocess.run([*base, "--dry-run"], cwd=ROOT, capture_output=True)
            self.assertEqual(dry.returncode, 0, dry.stderr.decode("utf-8", errors="replace"))
            self.assertNotIn("companion_delivery", json.loads(dry.stdout))
            self.assertFalse((companion / "published").exists())

            real = subprocess.run(base, cwd=ROOT, capture_output=True)
            self.assertEqual(real.returncode, 0, real.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(json.loads(real.stdout)["companion_delivery"]["status"], "delivered")
            self.assertTrue((companion / "published" / "manifest.json").is_file())
