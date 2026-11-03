"""Тесты автоматической семантической разметки и визуальной ленты скриншотов Kenshi."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.io_utils import canonical, digest, read_json
from tools.publisher import publish
from tools.screenshot_clustering import cluster_screenshots, compute_dhash, hamming_distance
from tools.screenshot_annotator import (
    annotate_single_screenshot,
    annotate_screenshots_batch,
    load_annotations_cache,
)
from tools.screenshot_feed import render_screenshot_feed, serialize_annotations_jsonl
from tools.validation import validate, validate_publication


def _create_dummy_image(path: Path, content: bytes, mtime: float | None = None) -> None:
    # Валидный минимальный заголовок JPEG
    data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + content + b"\xff\xd9"
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class ScreenshotAnnotationsTests(unittest.TestCase):
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
        """2. Группировка в эпизоды: последовательные кадры объединяются, при разрыве создается новая сцена."""
        f1 = self.screens_dir / "s1.jpg"
        f2 = self.screens_dir / "s2.jpg"
        f3 = self.screens_dir / "s3.jpg"
        _create_dummy_image(f1, b"content_a_1")
        _create_dummy_image(f2, b"content_a_2")
        _create_dummy_image(f3, b"content_b_different_scene")

        items = [
            {"filename": "s1.jpg", "source_path": f1, "sha256": "sha1", "mtime": 1000.0, "dhash": "0000000000000000"},
            {"filename": "s2.jpg", "source_path": f2, "sha256": "sha2", "mtime": 1020.0, "dhash": "0000000000000001"}, # dist 1, dt 20s -> та же сцена
            {"filename": "s3.jpg", "source_path": f3, "sha256": "sha3", "mtime": 2000.0, "dhash": "ffffffffffffffff"}, # dist 63, dt 980s -> новая сцена
        ]
        clustered, episodes = cluster_screenshots(items, time_threshold_seconds=120.0, hash_threshold_distance=10)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["episode_id"], "ep_001")
        self.assertEqual(episodes[0]["count"], 2)
        self.assertEqual(episodes[1]["episode_id"], "ep_002")
        self.assertEqual(episodes[1]["count"], 1)

    def test_annotations_schema_validation(self) -> None:
        """3. Каждая строка annotations.jsonl строго валидируется проектной схемой."""
        record = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": "fc7fc36767120d957787691d1109c6820b1a00a2a42ef27b21fbad09b5f0b262",
            "filename": "screenshot_1.jpg",
            "order": 1,
            "modified_at": "2026-10-30T10:00:00Z",
            "meta": {
                "game_day": 45,
                "game_time": "14:30",
                "scene_group_id": "ep_001",
                "is_exact_duplicate": False,
                "duplicate_of": None,
            },
            "ui_text": {
                "location": "Безродный",
                "characters": ["Хот Лон", "Бип"],
                "dialogues": [{"speaker": "Бип", "text": "Бип готов!", "confidence": 0.95}],
                "notifications": ["Прибытие"],
                "items_or_tasks": [],
            },
            "visual": {
                "scene_type": "travel",
                "present_characters": ["Хот Лон", "Бип"],
                "environment": "Туманные земли",
                "actions": ["Переход"],
                "summary": "Отряд вошел в зону Безродного.",
            },
            "provenance": {
                "source": "heuristic",
                "confidence": 0.85,
                "annotated_at": "2026-11-03T11:00:00Z",
            },
        }
        validate(record, "screenshot-annotations")

    def test_caching_annotator(self) -> None:
        """4. Неизмененные кадры используют кэш по SHA-256."""
        cache = {
            "cached_sha": {
                "format": "kenshi-screenshot-annotation/1",
                "sha256": "cached_sha",
                "filename": "old_name.jpg",
                "order": 1,
                "modified_at": "2026-10-25T10:00:00Z",
                "meta": {
                    "game_day": 10,
                    "game_time": "11:00",
                    "scene_group_id": "ep_old",
                    "is_exact_duplicate": False,
                    "duplicate_of": None,
                },
                "ui_text": {"location": "Хаб", "characters": ["Бип"], "dialogues": [], "notifications": [], "items_or_tasks": []},
                "visual": {"scene_type": "resting", "present_characters": ["Бип"], "environment": None, "actions": [], "summary": "Отдых"},
                "provenance": {"source": "gemini-3.8-flash", "confidence": 0.95, "annotated_at": "2026-10-25T10:00:00Z"},
            }
        }
        item = {
            "filename": "new_name.jpg",
            "sha256": "cached_sha",
            "order": 5,
            "modified_at": "2026-10-30T12:00:00Z",
            "scene_group_id": "ep_new",
            "is_exact_duplicate": False,
            "duplicate_of": None,
        }
        res = annotate_single_screenshot(item, cache=cache)
        self.assertEqual(res["order"], 5)
        self.assertEqual(res["modified_at"], "2026-10-30T12:00:00Z")
        self.assertEqual(res["meta"]["scene_group_id"], "ep_new")
        self.assertEqual(res["visual"]["summary"], "Отдых")

    def test_feed_rendering(self) -> None:
        """5. Генерация feed.md с эпизодами, репликами диалогов и ссылками."""
        ann = [{
            "format": "kenshi-screenshot-annotation/1",
            "sha256": "sha123",
            "filename": "shot_10.jpg",
            "order": 1,
            "modified_at": "2026-10-30T10:00:00Z",
            "meta": {"game_day": 50, "game_time": "15:00", "scene_group_id": "ep_001", "is_exact_duplicate": False, "duplicate_of": None},
            "ui_text": {"location": "Скуин", "characters": ["Хот Лон"], "dialogues": [{"speaker": "Шек", "text": "Слава Кралу!"}], "notifications": [], "items_or_tasks": []},
            "visual": {"scene_type": "dialogue", "present_characters": ["Хот Лон"], "environment": "Город шеков", "actions": ["Разговор"], "summary": "Встреча со стражей."},
            "provenance": {"source": "heuristic", "confidence": 0.7, "annotated_at": "2026-10-30T10:00:00Z"},
        }]
        episodes = [{
            "episode_id": "ep_001",
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
        self.assertIn("# Хронологическая визуальная лента эпизодов Kenshi", md)
        self.assertIn("### Эпизод 1: Скуин", md)
        self.assertIn("День 50, 15:00", md)
        self.assertIn("«Слава Кралу!»", md)
        self.assertIn("[shot_10.jpg](shot_10.jpg)", md)
        self.assertIn("![shot_10.jpg](shot_10.jpg)", md)

    def test_publish_pipeline_generates_annotations_and_feed(self) -> None:
        """6. Публикация автоматически создает annotations.jsonl и feed.md, валидация проходит."""
        from tests.test_screenshots import _create_sample_snapshot
        now = datetime.now(timezone.utc)
        snap_id = "test_snap_ann"
        _create_sample_snapshot(self.snapshots_dir, snap_id, day=50)

        # Создаем тестовый скриншот в окне
        shot = self.screens_dir / "screenshot_test.jpg"
        _create_dummy_image(shot, b"sample_visual_content", mtime=now.timestamp() - 3600)

        res = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self.assertTrue((self.published_dir / "screenshots/annotations.jsonl").is_file())
        self.assertTrue((self.published_dir / "screenshots/feed.md").is_file())

        # Валидация всей публикации
        summary = validate_publication(self.published_dir)
        self.assertEqual(summary["status"], "passed")
        self.assertIn("screenshot_annotations", summary["checks"])
