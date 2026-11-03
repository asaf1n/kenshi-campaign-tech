"""Автоматические тесты публикации скриншотов Kenshi.

Покрываемые требования:
1. Публикуются ключевые кадры за последние 7 суток, уменьшенные до 1600 px (при числе кадров окна до 20 публикуются все).
2. Изображения старше 7 суток исключаются.
3. Пограничное время ровно 7 суток обрабатывается корректно (включается).
4. Старые изображения удаляются из следующей публикации даже без новых скриншотов.
5. Оригинальные файлы остаются неизменными (read-only).
6. Повторная публикация без изменений идемпотентна.
7. Недоступный исходный каталог не приводит к потере опубликованных данных.
8. При ошибке копирования или валидации сохраняется предыдущая успешная публикация.
9. Индекс содержит корректные ссылки и хронологический порядок.
10. Публикация скриншотов не нарушает существующие контракты.
11. Защита от частично записанных изображений и изменений во время чтения.
12. Пустой каталог публикует пустой индекс и очищает ранее опубликованные изображения.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

try:
    from image_fixtures import write_test_image
except ImportError:  # запуск как tests.test_screenshots
    from tests.image_fixtures import write_test_image

from tools import screenshot_publish
from tools.io_utils import canonical, digest, read_json
from tools.publisher import PublisherError, publish
from tools.screenshots import (
    build_screenshots_index,
    collect_recent_screenshots,
    compute_file_sha256,
    render_screenshots_gallery,
)
from tools.validation import PublicationValidationError, validate, validate_publication


def _make_dummy_jpeg(path: Path, content: bytes = b"dummy_content", mtime: float | None = None) -> None:
    write_test_image(path, content, mtime=mtime)


def _make_dummy_png(path: Path, content: bytes = b"dummy_png", mtime: float | None = None) -> None:
    write_test_image(path, content, mtime=mtime)


def _pub(path: Path) -> str:
    """Имя опубликованного файла: исходное имя и префикс sha256 оригинала, расширение jpg."""
    return f"{path.stem}-{compute_file_sha256(path)[:8]}.jpg"


def _create_sample_snapshot(
    root: Path,
    snapshot_id: str,
    day: int = 15,
) -> Path:
    snap_dir = root / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    current_data = {
        "format": "kenshi-session/10",
        "save": {},
        "world": {"day": day, "time": "14:30", "money": 10000},
        "characters": [
            {
                "id": "ps:1",
                "name": "Хот Лон",
                "squad_id": "sq_main",
                "race": "Скелет",
                "status": {"dead": False, "unconscious": False},
                "attributes_base": {"Strength": 45.0},
                "skills_base": {"Melee Attack": 52.0},
                "condition": {"blood": 180.0, "hunger_raw": 3.0},
                "health": [],
                "weapons": [],
                "armour": {},
                "backpack": {"contents": []},
                "prosthetics": [],
                "inventory": [],
            }
        ],
        "squads": [{"squad_id": "sq_main", "squad_name": "Штурмовики", "members": ["Хот Лон"]}],
        "supplies": {},
        "warnings": [],
        "meta": {},
    }

    current_bytes = canonical(current_data)
    prov_data = {
        "format": "kenshi-snapshot/1",
        "snapshot_id": snapshot_id,
        "source_kind": "save",
        "source_format": "kenshi-session/10",
        "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "parent_snapshot_id": None,
        "branch_id": "main",
        "game_time": {"day": day, "time": "14:30"},
        "current_sha256": digest(current_bytes),
    }

    (snap_dir / "current.json").write_bytes(current_bytes)
    (snap_dir / "provenance.json").write_bytes(canonical(prov_data))
    return snap_dir


class ScreenshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.snapshots_dir = self.root / "snapshots"
        self.diffs_dir = self.root / "diffs"
        self.published_dir = self.root / "published"
        self.published_dir.mkdir(parents=True)
        (self.published_dir / ".gitkeep").write_bytes(b"")
        self.screens_dir = self.root / "screens"
        self.screens_dir.mkdir(parents=True)

        self.index_patcher = patch("tools.publisher.DEFAULT_INDEX_PATH", self.root / "index.json")
        self.index_patcher.start()
        self.archive_patcher = patch.object(screenshot_publish, "ARCHIVE_DIR", self.root / "archive")
        self.archive_patcher.start()

    def tearDown(self) -> None:
        self.archive_patcher.stop()
        self.index_patcher.stop()
        self.temp_dir.cleanup()

    def test_requirements_1_2_3_seven_day_window_and_boundary(self) -> None:
        """1. Публикуются все за 7 суток; 2. Старше 7 суток исключаются; 3. Ровно 7 суток включается."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        now_ts = now.timestamp()
        seven_days_sec = 7 * 24 * 3600

        # Файл 1: ровно сейчас
        _make_dummy_jpeg(self.screens_dir / "screenshot_1.jpg", b"1", mtime=now_ts)
        # Файл 2: 3 суток назад (внутри окна)
        _make_dummy_png(self.screens_dir / "screenshot_2.png", b"2", mtime=now_ts - 3 * 24 * 3600)
        # Файл 3: ровно 7 суток (пограничное время: age == 7 суток, включается)
        _make_dummy_jpeg(self.screens_dir / "screenshot_boundary.jpg", b"boundary", mtime=now_ts - seven_days_sec)
        # Файл 4: 7 суток и 1 секунда (старше окна, исключается)
        _make_dummy_jpeg(self.screens_dir / "screenshot_old.jpg", b"old", mtime=now_ts - seven_days_sec - 1.0)
        # Файл 5: 30 суток назад (исключается)
        _make_dummy_jpeg(self.screens_dir / "screenshot_ancient.jpg", b"ancient", mtime=now_ts - 30 * 24 * 3600)

        snap_id = "snap-window"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        res = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        self.assertEqual(res["status"], "published")
        self.assertEqual(res["screenshots_count"], 3)

        pub_screens = self.published_dir / "screenshots"
        self.assertTrue((pub_screens / "index.json").is_file())
        self.assertTrue((pub_screens / "index.md").is_file())
        for name in ("screenshot_1.jpg", "screenshot_2.png", "screenshot_boundary.jpg"):
            self.assertTrue((pub_screens / _pub(self.screens_dir / name)).is_file())
        self.assertFalse((pub_screens / _pub(self.screens_dir / "screenshot_old.jpg")).exists())
        self.assertFalse((pub_screens / _pub(self.screens_dir / "screenshot_ancient.jpg")).exists())

        idx_data = read_json((pub_screens / "index.json").read_bytes())
        self.assertEqual(idx_data["count"], 3)
        filenames = [s["filename"] for s in idx_data["screenshots"]]
        # Хронологический порядок: старый -> новый
        order = ("screenshot_boundary.jpg", "screenshot_2.png", "screenshot_1.jpg")
        self.assertEqual(filenames, [_pub(self.screens_dir / n) for n in order])

    def test_requirement_4_old_images_pruned_without_new_ones(self) -> None:
        """4. Старые изображения удаляются из следующей публикации даже без появления новых скриншотов."""
        t1 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        # Создаем скриншот 26 октября
        _make_dummy_jpeg(self.screens_dir / "screen_a.jpg", b"a", mtime=datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc).timestamp())

        snap_id = "snap-prune"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        # Публикация 1 (1 ноября: 26 октября входит в 7 дней)
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        pub_a = self.published_dir / "screenshots" / _pub(self.screens_dir / "screen_a.jpg")
        self.assertTrue(pub_a.is_file())

        # Публикация 2 (3 ноября: 26 октября стало > 7 дней; новых файлов НЕТ)
        t2 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=t2.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # Кадр должен быть удален из published/screenshots!
        self.assertFalse(pub_a.exists())
        idx_data = read_json((self.published_dir / "screenshots/index.json").read_bytes())
        self.assertEqual(idx_data["count"], 0)

    def test_requirement_5_original_files_remain_unchanged(self) -> None:
        """5. Оригинальные файлы в источнике остаются строго неизменными."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        file_path = self.screens_dir / "screenshot_original.jpg"
        _make_dummy_jpeg(file_path, b"immutable_content", mtime=now.timestamp() - 1000)

        stat_before = file_path.stat()
        sha_before = compute_file_sha256(file_path)

        snap_id = "snap-orig"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        stat_after = file_path.stat()
        sha_after = compute_file_sha256(file_path)

        self.assertEqual(stat_before.st_size, stat_after.st_size)
        self.assertEqual(stat_before.st_mtime, stat_after.st_mtime)
        self.assertEqual(sha_before, sha_after)

    def test_requirement_6_idempotent_republish(self) -> None:
        """6. Повторная публикация без изменений идемпотентна."""
        now = datetime.now(timezone.utc)
        _make_dummy_jpeg(self.screens_dir / "screenshot_idem.jpg", b"idem", mtime=now.timestamp() - 500)

        snap_id = "snap-idem"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        res1 = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
        )

        manifest1 = (self.published_dir / "manifest.json").read_bytes()
        index1 = (self.published_dir / "screenshots/index.json").read_bytes()

        # Повторный запуск с тем же состоянием
        res2 = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
        )

        manifest2 = (self.published_dir / "manifest.json").read_bytes()
        index2 = (self.published_dir / "screenshots/index.json").read_bytes()

        self.assertEqual(manifest1, manifest2)
        self.assertEqual(index1, index2)

    def test_requirements_7_and_8_inaccessible_dir_and_rollback(self) -> None:
        """7. Недоступный исходный каталог не удаляет данные; 8. При ошибке сохраняется прошлая публикация."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        _make_dummy_jpeg(self.screens_dir / "screenshot_init.jpg", b"init", mtime=now.timestamp() - 100)

        snap_id = "snap-err"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        # 1. Успешная публикация
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        prev_manifest = (self.published_dir / "manifest.json").read_bytes()
        init_screen = self.published_dir / "screenshots" / _pub(self.screens_dir / "screenshot_init.jpg")
        prev_screen = init_screen.read_bytes()

        # 2. Несуществующий каталог
        bad_dir = self.root / "non_existent_screens"
        with self.assertRaises(PublisherError):
            publish(
                snapshot_id=snap_id,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=self.published_dir,
                screens_dir=bad_dir,
            )

        # Предыдущая публикация сохранена нетронутой!
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), prev_manifest)
        self.assertEqual(init_screen.read_bytes(), prev_screen)

    def test_requirement_9_index_structure_order_and_urls(self) -> None:
        """9. Индекс содержит корректные ссылки, хеши и хронологический порядок."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        now_ts = now.timestamp()
        _make_dummy_jpeg(self.screens_dir / "b_earlier.jpg", b"content_b", mtime=now_ts - 2000)
        _make_dummy_jpeg(self.screens_dir / "a_later.jpg", b"content_a", mtime=now_ts - 500)

        snap_id = "snap-idx"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        idx_data = read_json((self.published_dir / "screenshots/index.json").read_bytes())
        validate(idx_data, "screenshots-index")

        items = idx_data["screenshots"]
        self.assertEqual(len(items), 2)
        # b_earlier.jpg был раньше, значит order 1
        self.assertEqual(items[0]["order"], 1)
        b_name, a_name = _pub(self.screens_dir / "b_earlier.jpg"), _pub(self.screens_dir / "a_later.jpg")
        self.assertEqual(items[0]["filename"], b_name)
        self.assertEqual(items[0]["path"], f"screenshots/{b_name}")
        self.assertTrue(items[0]["is_keyframe"])
        self.assertEqual(items[0]["source_sha256"], compute_file_sha256(self.screens_dir / "b_earlier.jpg"))
        self.assertIn("https://raw.githubusercontent.com/asaf1n/Kenshi-Compain/", items[0]["url_raw"])
        self.assertIn("https://github.com/asaf1n/Kenshi-Compain/", items[0]["url_blob"])
        self.assertEqual(items[0]["sha256"], compute_file_sha256(self.published_dir / "screenshots" / b_name))

        self.assertEqual(items[1]["order"], 2)
        self.assertEqual(items[1]["filename"], a_name)

        # Проверка Markdown-галереи
        gallery_text = (self.published_dir / "screenshots/index.md").read_text(encoding="utf-8")
        self.assertIn(f"### 1. {b_name}", gallery_text)
        self.assertIn(f"### 2. {a_name}", gallery_text)
        self.assertIn(f"![{b_name}]({b_name})", gallery_text)

    def test_requirement_10_compatibility_and_validation(self) -> None:
        """10. Публикация скриншотов полностью удовлетворяет validate_publication."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        _make_dummy_jpeg(self.screens_dir / "screenshot_test.jpg", b"test", mtime=now.timestamp() - 50)

        snap_id = "snap-val-compat"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        summary = validate_publication(self.published_dir)
        self.assertEqual(summary["status"], "passed")
        self.assertIn("screenshots", summary["checks"])

    def test_protection_partially_written_and_modifying_files(self) -> None:
        """11. Защита от частично записанных файлов (0 байт, битый заголовок)."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        now_ts = now.timestamp()

        # 0 байт
        (self.screens_dir / "screenshot_empty.jpg").write_bytes(b"")
        os.utime(self.screens_dir / "screenshot_empty.jpg", (now_ts - 100, now_ts - 100))

        # Невалидный заголовок
        (self.screens_dir / "screenshot_corrupt.jpg").write_bytes(b"NOT_A_JPEG_FILE")
        os.utime(self.screens_dir / "screenshot_corrupt.jpg", (now_ts - 100, now_ts - 100))

        # Валидный файл
        _make_dummy_jpeg(self.screens_dir / "screenshot_good.jpg", b"good", mtime=now_ts - 100)

        collected = collect_recent_screenshots(
            source_dir=self.screens_dir,
            cutoff_time=now - timedelta(days=7),
            now_time=now,
        )

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["filename"], "screenshot_good.jpg")

    def test_empty_catalog_publishes_empty_index_and_deletes_old(self) -> None:
        """12. Доступный, но пустой каталог публикует пустой индекс и удаляет старые файлы."""
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        _make_dummy_jpeg(self.screens_dir / "screen_to_remove.jpg", b"remove_me", mtime=now.timestamp() - 100)

        snap_id = "snap-empty-test"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        # 1. Первая публикация с 1 файлом
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        removed = self.published_dir / "screenshots" / _pub(self.screens_dir / "screen_to_remove.jpg")
        self.assertTrue(removed.is_file())

        # 2. Очищаем исходный каталог (теперь он доступен, но пуст)
        (self.screens_dir / "screen_to_remove.jpg").unlink()

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # screen_to_remove.jpg удален
        self.assertFalse(removed.exists())
        idx_data = read_json((self.published_dir / "screenshots/index.json").read_bytes())
        self.assertEqual(idx_data["count"], 0)
        gallery_text = (self.published_dir / "screenshots/index.md").read_text(encoding="utf-8")
        self.assertIn("новые скриншоты не зафиксированы", gallery_text)


    def test_keyframes_are_limited_downscaled_and_archived(self) -> None:
        """13. В выпуск идут не более 30 ключевых кадров не шире 1600 px; весь набор окна сохранён в архиве."""
        import av

        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        for n in range(45):
            size = (3200, 2000) if n == 0 else (64, 48)
            write_test_image(
                self.screens_dir / f"screenshot_{n}.jpg", f"frame{n}".encode(), size=size,
                mtime=now.timestamp() - 86400 * 3 + n * 600,
            )
        snap_id = "snap-keyframes"
        _create_sample_snapshot(self.snapshots_dir, snap_id)
        publish(
            snapshot_id=snap_id, snapshots_dir=self.snapshots_dir, diffs_dir=self.diffs_dir,
            published_dir=self.published_dir, screens_dir=self.screens_dir,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        idx_data = read_json((self.published_dir / "screenshots/index.json").read_bytes())
        self.assertEqual(idx_data["count"], 30)
        self.assertEqual(len(list((self.root / "archive").glob("*.jpg"))), 45)
        first = idx_data["screenshots"][0]
        self.assertEqual(first["filename"], _pub(self.screens_dir / "screenshot_0.jpg"))
        with av.open(str(self.published_dir / "screenshots" / first["filename"])) as container:
            frame = next(container.decode(video=0))
        self.assertEqual((frame.width, frame.height), (1600, 1000))


if __name__ == "__main__":
    unittest.main()
