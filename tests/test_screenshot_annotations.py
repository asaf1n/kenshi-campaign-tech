"""Тесты технического экстрактора скриншотов Kenshi и технической хронологической ленты."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.io_utils import read_json
from tools.publisher import publish
from tools.screenshot_clustering import cluster_screenshots, compute_dhash, hamming_distance
from tools.screenshot_extractor import (
    extract_single_screenshot,
    extract_screenshots_batch,
    load_extractor_cache,
)
from tools.screenshot_feed import render_screenshot_feed, serialize_annotations_jsonl
from tools.validation import validate, validate_publication


def _create_dummy_image(path: Path, content: bytes, mtime: float | None = None) -> None:
    # Валидный минимальный заголовок JPEG
    data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + content + b"\xff\xd9"
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class ScreenshotExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.screens_dir = self.tmp_path / "game_screens"
        self.screens_dir.mkdir(parents=True, exist_ok=True)
        self.published_dir = self.tmp_path / "published"
        self.snapshots_dir = self.tmp_path / "snapshots"
        self.diffs_dir = self.tmp_path / "diffs"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.diffs_dir.mkdir(parents=True, exist_ok=True)
        # Реестр снимков теста изолирован: публикация не должна менять реальный data/index.json.
        index_patcher = patch("tools.publisher.DEFAULT_INDEX_PATH", self.tmp_path / "index.json")
        index_patcher.start()
        self.addCleanup(index_patcher.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dhash_and_exact_duplicates(self) -> None:
        """1. Точные дубликаты по SHA-256 и расчет расстояния Хэмминга."""
        f1 = self.screens_dir / "shot_1.jpg"
        f2 = self.screens_dir / "shot_2.jpg"
        _create_dummy_image(f1, b"ident_bytes")
        _create_dummy_image(f2, b"ident_bytes")

        h1 = compute_dhash(f1)
        h2 = compute_dhash(f2)
        self.assertEqual(h1, h2)
        self.assertEqual(hamming_distance(h1, h2), 0)

        items = [
            {"filename": f1.name, "path": f1, "source_path": f1, "sha256": "abc", "mtime": 1000.0},
            {"filename": f2.name, "path": f2, "source_path": f2, "sha256": "abc", "mtime": 1010.0},
        ]
        clustered, episodes = cluster_screenshots(items)
        self.assertEqual(len(clustered), 2)
        self.assertFalse(clustered[0]["is_exact_duplicate"])
        self.assertTrue(clustered[1]["is_exact_duplicate"])
        self.assertEqual(clustered[1]["duplicate_of"], "abc")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["count"], 2)

    def test_clustering_scene_boundaries(self) -> None:
        """2. Группировка в серии: последовательные похожие кадры объединяются, при разрыве создается новая серия."""
        f1 = self.screens_dir / "s1.jpg"
        f2 = self.screens_dir / "s2.jpg"
        f3 = self.screens_dir / "s3.jpg"
        _create_dummy_image(f1, b"content_a_1")
        _create_dummy_image(f2, b"content_a_2")
        _create_dummy_image(f3, b"content_b_different_scene")

        items = [
            {"filename": "s1.jpg", "source_path": f1, "sha256": "sha1", "mtime": 1000.0, "dhash": "0000000000000000"},
            {"filename": "s2.jpg", "source_path": f2, "sha256": "sha2", "mtime": 1020.0, "dhash": "0000000000000001"},
            {"filename": "s3.jpg", "source_path": f3, "sha256": "sha3", "mtime": 2000.0, "dhash": "ffffffffffffffff"},
        ]
        clustered, episodes = cluster_screenshots(items, time_threshold_seconds=120.0, hash_threshold_distance=10)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["count"], 2)
        self.assertEqual(episodes[1]["count"], 1)

    def test_annotations_schema_validation(self) -> None:
        """3. Каждая строка annotations.jsonl строго валидируется технической схемой без генераций."""
        record = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": "fc7fc36767120d957787691d1109c6820b1a00a2a42ef27b21fbad09b5f0b262",
            "filename": "screenshot_1.jpg",
            "order": 1,
            "modified_at": "2026-10-30T10:00:00Z",
            "meta": {
                "dhash": "091f1f1b9b9f6f6f",
                "scene_group_id": "seq_001",
                "is_exact_duplicate": False,
                "duplicate_of": None,
            },
            "ocr": {
                "game_day": 45,
                "game_time": "14:30",
                "location": "Безродный",
                "characters": ["Хот Лон", "Бип"],
                "dialogues": [{"speaker": "Бип", "text": "Бип готов!", "confidence": 0.95}],
                "notifications": ["Прибытие"],
                "items_or_tasks": [],
            },
            "provenance": {
                "source": "gemini-3.8-flash",
                "confidence": 0.85,
                "extracted_at": "2026-11-03T11:00:00Z",
            },
        }
        validate(record, "screenshot-annotations")

    def test_uncertainty_preservation_no_heuristics(self) -> None:
        """4. При отсутствии OCR сохраняется неопределенность (null / []), без эвристических описаний."""
        item = {
            "filename": "raw_shot.jpg",
            "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "order": 1,
            "modified_at": "2026-10-30T12:00:00Z",
            "dhash": "0123456789abcdef",
            "scene_group_id": "seq_001",
            "is_exact_duplicate": False,
            "duplicate_of": None,
        }
        # Вызов без API-ключа имитирует отсутствие OCR
        res = extract_single_screenshot(item, cache={}, api_key=None)
        self.assertEqual(res["provenance"]["source"], "metadata")
        self.assertIsNone(res["provenance"]["confidence"])
        self.assertIsNone(res["ocr"]["game_day"])
        self.assertIsNone(res["ocr"]["game_time"])
        self.assertIsNone(res["ocr"]["location"])
        self.assertEqual(res["ocr"]["characters"], [])
        self.assertEqual(res["ocr"]["dialogues"], [])
        self.assertNotIn("visual", res)  # Блок visual полностью исключен
        validate(res, "screenshot-annotations")

    def test_caching_extractor(self) -> None:
        """5. Неизмененные кадры используют кэш по SHA-256."""
        cache = {
            "cached_sha": {
                "format": "kenshi-screenshot-annotation/1",
                "sha256": "cached_sha",
                "filename": "old_name.jpg",
                "order": 1,
                "modified_at": "2026-10-25T10:00:00Z",
                "meta": {
                    "dhash": "0011223344556677",
                    "scene_group_id": "seq_old",
                    "is_exact_duplicate": False,
                    "duplicate_of": None,
                },
                "ocr": {"game_day": 10, "game_time": "11:00", "location": "Хаб", "characters": ["Бип"], "dialogues": [], "notifications": [], "items_or_tasks": []},
                "provenance": {"source": "gemini-3.8-flash", "confidence": 0.95, "extracted_at": "2026-10-25T10:00:00Z"},
            }
        }
        item = {
            "filename": "new_name.jpg",
            "sha256": "cached_sha",
            "order": 5,
            "modified_at": "2026-10-30T12:00:00Z",
            "dhash": "0011223344556677",
            "scene_group_id": "seq_new",
            "is_exact_duplicate": False,
            "duplicate_of": None,
        }
        res = extract_single_screenshot(item, cache=cache)
        self.assertEqual(res["order"], 5)
        self.assertEqual(res["modified_at"], "2026-10-30T12:00:00Z")
        self.assertEqual(res["meta"]["scene_group_id"], "seq_new")
        self.assertEqual(res["ocr"]["location"], "Хаб")

    def test_technical_feed_rendering(self) -> None:
        """6. Генерация feed.md фиксирует технические серии, OCR и ссылки без вымышленных нарративов."""
        ann = [{
            "format": "kenshi-screenshot-annotation/1",
            "sha256": "sha123",
            "filename": "shot_10.jpg",
            "order": 1,
            "modified_at": "2026-10-30T10:00:00Z",
            "meta": {"dhash": "0000000000000000", "scene_group_id": "seq_001", "is_exact_duplicate": False, "duplicate_of": None},
            "ocr": {"game_day": 50, "game_time": "15:00", "location": "Скуин", "characters": ["Хот Лон"], "dialogues": [{"speaker": "Шек", "text": "Слава Кралу!"}], "notifications": [], "items_or_tasks": []},
            "provenance": {"source": "gemini-3.8-flash", "confidence": 0.9, "extracted_at": "2026-10-30T10:00:00Z"},
        }]
        episodes = [{
            "episode_id": "seq_001",
            "episode_index": 1,
            "count": 1,
            "key_filename": "shot_10.jpg",
            "key_sha256": "sha123",
            "filenames": ["shot_10.jpg"],
            "start_time": "2026-10-30T10:00:00Z",
            "end_time": "2026-10-30T10:00:00Z",
            "exact_duplicates_count": 0,
        }]
        md = render_screenshot_feed(ann, episodes, generated_at="2026-10-30T12:00:00Z", window_days=7)
        self.assertIn("# Техническая лента скриншотов Kenshi", md)
        self.assertIn("### Серия 1", md)
        self.assertIn("**Локация (OCR)**: Скуин", md)
        self.assertIn("**Игровое время (OCR)**: `День 50, 15:00`", md)
        self.assertIn("«Слава Кралу!»", md)
        self.assertIn("[shot_10.jpg](shot_10.jpg)", md)
        self.assertIn("![shot_10.jpg](shot_10.jpg)", md)
        self.assertNotIn("Краткое описание", md)
        self.assertNotIn("Тип сцены", md)

    def test_ocr_content_criterion(self) -> None:
        """8. OCR содержателен, если непусто любое поле контракта (даже один game_day); иначе выпуск без OCR-артефактов."""
        from tools.screenshot_publish import OCR_FIELDS, has_ocr_content

        empty = {"dialogues": [], "characters": [], "location": None, "game_day": None,
                 "game_time": None, "notifications": [], "items_or_tasks": []}
        self.assertFalse(has_ocr_content([]))
        self.assertFalse(has_ocr_content([{"ocr": dict(empty)}, {"ocr": dict(empty, location="")}]))
        for field in OCR_FIELDS:
            value = [{"text": "x"}] if empty[field] == [] else (12 if field == "game_day" else "x")
            with self.subTest(field=field):
                self.assertTrue(has_ocr_content([{"ocr": dict(empty)}, {"ocr": dict(empty, **{field: value})}]))

    def test_publish_keeps_artifacts_for_single_ocr_field(self) -> None:
        """9. Даже один распознанный game_day или game_time сохраняет аннотации, ленту и галерею в выпуске."""
        from tests.image_fixtures import write_test_image
        from tests.test_screenshots import _create_sample_snapshot
        from tools import screenshot_extractor, screenshot_publish

        real_extract = screenshot_extractor.extract_screenshots_batch
        for field, value in (("game_day", 12), ("game_time", "15:06")):
            with self.subTest(field=field):
                root = self.tmp_path / field
                screens, published = root / "screens", root / "published"
                snapshots, diffs = root / "snapshots", root / "diffs"
                for directory in (screens, published, snapshots, diffs):
                    directory.mkdir(parents=True)
                _create_sample_snapshot(snapshots, f"snap_{field}", day=50)
                now = datetime.now(timezone.utc)
                write_test_image(screens / "shot.jpg", field.encode(), mtime=now.timestamp() - 3600)

                def fake_extract(**kwargs):
                    records = real_extract(**kwargs)
                    records[0]["ocr"][field] = value
                    return records

                with patch.object(screenshot_publish, "ARCHIVE_DIR", root / "archive"), \
                        patch.object(screenshot_extractor, "extract_screenshots_batch", fake_extract):
                    publish(snapshot_id=f"snap_{field}", snapshots_dir=snapshots, diffs_dir=diffs,
                            published_dir=published, screens_dir=screens,
                            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))

                names = sorted(path.name for path in (published / "screenshots").iterdir() if path.suffix != ".jpg")
                self.assertEqual(names, ["annotations.jsonl", "feed.md", "index.json", "index.md", "screenshots_latest.pdf"])
                artifacts = read_json((published / "manifest.json").read_bytes())["artifacts"]
                self.assertEqual(artifacts["screenshot_annotations"], "screenshots/annotations.jsonl")
                self.assertEqual(artifacts["screenshot_feed"], "screenshots/feed.md")
                self.assertEqual(validate_publication(published)["status"], "passed")

    def test_publish_pipeline_omits_empty_ocr_artifacts(self) -> None:
        """7. Публикация кадра без распознанного текста не создаёт annotations.jsonl и feed.md; валидация проходит."""
        from tests.test_screenshots import _create_sample_snapshot
        now = datetime.now(timezone.utc)
        snap_id = "test_snap_ann"
        _create_sample_snapshot(self.snapshots_dir, snap_id, day=50)

        from tests.image_fixtures import write_test_image
        from tools import screenshot_publish
        archive_patcher = patch.object(screenshot_publish, "ARCHIVE_DIR", self.tmp_path / "archive")
        archive_patcher.start()
        self.addCleanup(archive_patcher.stop)

        shot = self.screens_dir / "screenshot_test.jpg"
        write_test_image(shot, b"sample_visual_content", mtime=now.timestamp() - 3600)

        res = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        # Реальный кадр без распознанного текста: аннотации и лента не публикуются, индексы остаются.
        pub_screens = self.published_dir / "screenshots"
        self.assertTrue((pub_screens / "index.json").is_file())
        self.assertTrue((pub_screens / "index.md").is_file())
        for name in ("annotations.jsonl", "feed.md"):
            self.assertFalse((pub_screens / name).exists())

        summary = validate_publication(self.published_dir)
        self.assertEqual(summary["status"], "passed")
        self.assertNotIn("screenshot_annotations", summary["checks"])
