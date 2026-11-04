"""Дельта кадров, index.md, changes.md и screenshots_latest.pdf на сквозной публикации."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.image_fixtures import write_test_image
from tests.test_publish import _create_sample_snapshot
from tools import screenshot_publish
from tools.changes_render import render_changes_md
from tools.io_utils import canonical, read_json
from tools.publisher import publish
from tools.screenshot_delta import bind_interval, delta_boundary, new_frame_names
from tools.screenshot_pdf import build_screenshots_pdf
from tools.timeline import project_timeline

PARENT, CHILD = "snap_parent", "snap_child"


class ScreenshotDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.snapshots = self.tmp / "data/snapshots"
        self.diffs = self.tmp / "data/diffs"
        self.published = self.tmp / "published"
        self.screens = self.tmp / "screens"
        for path in (self.snapshots, self.diffs, self.screens):
            path.mkdir(parents=True)
        patcher = patch.object(screenshot_publish, "ARCHIVE_DIR", self.tmp / "archive")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _publish_pair(self, now: datetime, generated: datetime | None = None) -> dict:
        stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        _create_sample_snapshot(self.snapshots, PARENT, day=10, money=100)
        _create_sample_snapshot(self.snapshots, CHILD, day=11, money=50, parent_snapshot_id=PARENT)
        entry = {"branch_id": "main", "campaign_id": "test", "source_kind": "save", "world_ref_id": None}
        index = {
            "format": "kenshi-index/1", "active_branch_id": "main", "updated_at": stamp(now),
            "branches": {"main": {"name": "main", "created_at": stamp(now), "head_snapshot_id": CHILD}},
            "snapshots": {
                PARENT: {**entry, "source_sha256": "0" * 64, "snapshot_id": PARENT, "parent_snapshot_id": None, "game_time": {"day": 10, "time": "14:30"},
                         "registered_at": stamp(now - timedelta(hours=2))},
                CHILD: {**entry, "source_sha256": "1" * 64, "snapshot_id": CHILD, "parent_snapshot_id": PARENT, "game_time": {"day": 11, "time": "14:30"},
                        "registered_at": stamp(now - timedelta(minutes=1))},
            },
        }
        (self.tmp / "data/index.json").write_bytes(canonical(index))
        return publish(snapshot_id=CHILD, snapshots_dir=self.snapshots, diffs_dir=self.diffs, published_dir=self.published,
                       parent_snapshot_id=PARENT, screens_dir=self.screens, index_path=self.tmp / "data/index.json",
                       generated_at=stamp(generated or now))

    def test_publication_splits_delta_and_stays_idempotent(self) -> None:
        """Ловит смешение дельты с хвостом, потерю raw-ссылок и смену generated_at при повторе из-за шапки brief.md."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        write_test_image(self.screens / "screenshot_1.jpg", b"old", mtime=(now - timedelta(hours=5)).timestamp())
        write_test_image(self.screens / "screenshot_2.jpg", b"new", mtime=(now - timedelta(hours=1)).timestamp())
        first = self._publish_pair(now)

        index = read_json((self.published / "screenshots/index.json").read_bytes())
        new_name = index["new_frames"][0]
        self.assertEqual(len(index["new_frames"]), 1)
        self.assertTrue(new_name.startswith("screenshot_2-"))

        index_md = (self.published / "screenshots/index.md").read_text(encoding="utf-8")
        delta_part, tail_part = index_md.split("## Б. Архивный хвост")
        self.assertIn(new_name, delta_part)
        self.assertIn("screenshot_1-", tail_part)
        self.assertIn(f"published/screenshots/{new_name})", delta_part)
        self.assertIn(f"`published/screenshots/{new_name}`", delta_part)
        self.assertIn("`published/screenshots/screenshots_latest.pdf`", index_md)
        self.assertIn("Казна: 100 -> 50 (-50)", delta_part)

        brief = (self.published / "brief.md").read_text(encoding="utf-8")
        self.assertIn(f"`{CHILD}`", brief[:400])
        self.assertIn(f"`{PARENT}`", brief[:400])
        self.assertIn(first["manifest"]["generated_at"], brief[:400])
        self.assertIn("## Скриншоты снимка", brief)
        self.assertIn(new_name, brief)
        self.assertNotIn("screenshot_1-", brief)
        self.assertTrue((self.published / "changes.md").is_file())

        pdf_path = self.published / "screenshots/screenshots_latest.pdf"
        pdf = pdf_path.read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(pdf.count(b"/Type /Page\n") or pdf.count(b"/Type /Page "), 1)

        second = self._publish_pair(now, now + timedelta(minutes=5))
        self.assertEqual(second["manifest"]["generated_at"], first["manifest"]["generated_at"])
        self.assertEqual((self.published / "brief.md").read_text(encoding="utf-8"), brief)
        self.assertEqual(pdf_path.read_bytes(), pdf)

    def test_interval_binding_and_delta_boundary(self) -> None:
        """Ловит сдвиг границы: кадр в секунду регистрации родителя относится к прошлой выгрузке."""
        chain = [{"snapshot_id": "a", "registered_at": "2026-11-01T10:00:00Z"},
                 {"snapshot_id": "b", "registered_at": "2026-11-01T12:00:00Z"}]
        cases = [("2026-11-01T09:00:00Z", None, "a"), ("2026-11-01T10:00:00Z", "a", "b"),
                 ("2026-11-01T11:00:00Z", "a", "b"), ("2026-11-01T13:00:00Z", "b", None)]
        for moment, before, after in cases:
            with self.subTest(moment=moment):
                left, right = bind_interval(moment, chain)
                self.assertEqual((left or {}).get("snapshot_id"), before)
                self.assertEqual((right or {}).get("snapshot_id"), after)
        items = [{"filename": "x", "modified_at": "2026-11-01T10:00:00Z"}, {"filename": "y", "modified_at": "2026-11-01T10:00:01Z"}]
        self.assertEqual(new_frame_names(items, "2026-11-01T10:00:00Z"), ["y"])
        self.assertEqual(new_frame_names(items, None), [])

    def test_repeat_run_on_same_save_keeps_delta_boundary(self) -> None:
        """Ловит обнуление дельты: родитель входит в идентичность, второй запуск на том же сейве даёт новый снимок."""
        index = {"snapshots": {
            "a": {"source_sha256": "1", "registered_at": "2026-11-01T10:00:00Z", "parent_snapshot_id": None},
            "b": {"source_sha256": "2", "registered_at": "2026-11-01T12:00:00Z", "parent_snapshot_id": "a"},
            "c": {"source_sha256": "2", "registered_at": "2026-11-01T12:05:00Z", "parent_snapshot_id": "b"},
        }}
        self.assertEqual(delta_boundary(index, "b", "a"), "2026-11-01T10:00:00Z")
        self.assertEqual(delta_boundary(index, "c", "b"), "2026-11-01T10:00:00Z")
        self.assertIsNone(delta_boundary(index, "a", None))

    def test_pdf_skips_truncated_frame(self) -> None:
        """Ловит срыв публикации кадром с верной сигнатурой, который игра ещё дописывает."""
        good, broken = self.screens / "good.jpg", self.screens / "broken.jpg"
        write_test_image(good, b"good", mtime=1_800_000_000)
        broken.write_bytes(good.read_bytes()[:200])
        target = self.tmp / "out.pdf"
        frames = [{"source_path": good, "filename": "good.jpg", "pdf_header": "Кадр"},
                  {"source_path": broken, "filename": "broken.jpg", "pdf_header": "Кадр"}]
        self.assertEqual(build_screenshots_pdf(frames, target), ["broken.jpg"])
        self.assertTrue(target.read_bytes().startswith(b"%PDF"))

    def test_changes_md_expands_intermediate_diff_referenced_by_timeline(self) -> None:
        """Ловит слепую ссылку data/diffs/: событие прошлого перехода ведёт на существующий раздел changes.md."""
        old_child = "sha256-" + "a" * 64
        event = {
            "format": "kenshi-event/1", "event_id": "auto-1", "session_id": "auto", "recorded_at": "2026-11-01T10:00:00Z",
            "kind": "state_delta", "status": "observed", "summary": "Казна", "snapshot_id": old_child,
            "evidence": [{"kind": "diff", "ref": f"data/diffs/sha256-{'b' * 64}__{old_child}.json"}],
        }
        projected = project_timeline([event], "sha256-" + "c" * 64, "sha256-x__sha256-y")
        ref = projected[0]["evidence"][0]["ref"]
        self.assertEqual(ref, "changes.md#snapshot-aaaaaaaaaaaa")

        text = render_changes_md(
            {"valid": True, "from": {"day": 1, "time": "00:00", "snapshot_id": "p"}, "to": {"day": 2, "time": "00:00", "snapshot_id": "c"},
             "world": {"money": {"from": 10, "to": 7, "delta": -3}},
             "prosthetics": [{"id": "ps:1", "name": "Рука", "changes": {"left_leg": {"from": None, "to": "lost"}}}]},
            [{"child_id": old_child, "label": "Day 1, 12:00", "summaries": ["Казна: 1 -> 2 (+1)"]}],
        )
        self.assertIn('<a id="snapshot-aaaaaaaaaaaa"></a>', text)
        self.assertIn("Казна: 1 -> 2 (+1)", text)
        self.assertIn("left_leg: None -> lost", text)
        self.assertNotIn("data/diffs/", text)


if __name__ == "__main__":
    unittest.main()
