"""Синхронизация с Google Диском: перезапись на месте, сбор каталога сессии и приём пакетов."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.config import find_gdrive_root
from tools.gdrive_sync import ingest_inbox, sync_chronicle, sync_drive, sync_session, write_in_place


class GDriveSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.root = self.tmp / "drive"
        self.root.mkdir()

    def test_write_in_place_keeps_file_identity(self) -> None:
        """Ловит подмену файла при записи: новый идентификатор файла разрывает привязку file_id на Диске."""
        target = self.root / "published" / "brief.md"
        self.assertTrue(write_in_place(b"first", target))
        identity = os.stat(target).st_ino
        stamp = os.stat(target).st_mtime_ns
        self.assertFalse(write_in_place(b"first", target))
        self.assertEqual(os.stat(target).st_mtime_ns, stamp)
        self.assertTrue(write_in_place(b"second, longer", target))
        self.assertEqual(os.stat(target).st_ino, identity)
        self.assertEqual(target.read_bytes(), b"second, longer")

    def test_session_layout_overflow_frames_and_missing_drive(self) -> None:
        """Ловит потерю кадров дельты вне выпуска и отказ публикации при неподключённом Диске."""
        published = self.tmp / "published"
        screens = published / "screenshots"
        screens.mkdir(parents=True)
        for name in ("brief.md", "squad.md", "changes.md", "changes.json", "timeline.jsonl", "manifest.json"):
            (published / name).write_text(name, encoding="utf-8")
        (screens / "index.md").write_text("index", encoding="utf-8")
        (screens / "screenshots_latest.pdf").write_bytes(b"%PDF")
        (screens / "a-1.jpg").write_bytes(b"a")
        (screens / "index.json").write_text(json.dumps({"new_frames": ["a-1.jpg", "b-2.jpg"]}), encoding="utf-8")
        archive = self.tmp / "archive"
        archive.mkdir()
        (archive / "b-2.jpg").write_bytes(b"b")

        report = sync_session(published, self.root, archive)
        session = self.root / "published"
        self.assertEqual(report["errors"], [])
        self.assertEqual((session / "screenshots_index.md").read_text(encoding="utf-8"), "index")
        self.assertTrue((session / "screenshots_latest.pdf").is_file())
        self.assertEqual(sorted(p.name for p in (session / "screenshots").iterdir()), ["a-1.jpg", "b-2.jpg", "index.md"])
        self.assertEqual(sync_session(published, self.root, archive)["written"], 0)

        self.assertEqual(sync_drive(self.tmp / "absent", published, None)["status"], "skipped")
        self.assertIsNone(find_gdrive_root({}, (self.tmp / "absent",)))
        self.assertEqual(find_gdrive_root({}, (self.tmp / "absent", self.root)), self.root)

    def test_chronicle_mirror_and_inbox_packets(self) -> None:
        """Ловит запись вне Campaign/, частичное применение пакета и перезапись неизменяемого артефакта сессии."""
        companion = self.tmp / "companion"
        sessions = companion / "Campaign" / "Хроники" / "archive" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "S-20261101-1.md").write_text("старый\n", encoding="utf-8")
        (companion / "Campaign" / "Хроники" / "Арка.md").write_text("канон\n", encoding="utf-8")
        inbox = self.root / "tmp"
        inbox.mkdir()

        good = ("=== ФАЙЛ: Campaign/Хроники/Арка.md ===\nновый канон\n=== Глава 2 ===\nпродолжение\n=== КОНЕЦ ===\n"
                "=== ВЫГРУЗКА S-20261102-1 ===\n---\ncampaign: Хроники\n---\nтекст\n"
                "=== ПРАВКИ: Арка.md ===\nвручную\n=== КОНЕЦ ===\n")
        (inbox / "good.md").write_text(good, encoding="utf-8")
        (inbox / "escape.md").write_text(
            "=== ФАЙЛ: Campaign/Хроники/Новый.md ===\nа\n=== КОНЕЦ ===\n=== ФАЙЛ: Campaign/../AGENTS.md ===\nб\n=== КОНЕЦ ===\n",
            encoding="utf-8")
        (inbox / "immutable.md").write_text(
            "=== ВЫГРУЗКА S-20261101-1 ===\n---\ncampaign: Хроники\n---\nподмена\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "bypass.md").write_text(
            "=== ФАЙЛ: Campaign/Хроники/archive/sessions/S-20261101-1.md ===\nподмена\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "cut.md").write_text("=== ФАЙЛ: Campaign/Хроники/Обрезок.md ===\nполовина\n", encoding="utf-8")
        (inbox / "partial.md").write_text(
            "=== ФАЙЛ: Campaign/Хроники/Первый.md ===\nа\n=== КОНЕЦ ===\n"
            "=== ФАЙЛ: Campaign/Хроники/Первый.md/вложенный.md ===\nб\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "short.md").write_text("=== ФАЙЛ: Короткий.md ===\nв\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "note.txt").write_text("обычная заметка", encoding="utf-8")

        report = ingest_inbox(self.root, companion, now=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc))

        self.assertEqual([item["packet"] for item in report["processed"]], ["good.md", "short.md"])
        self.assertEqual(report["processed"][0]["manual"], ["ПРАВКИ: Арка.md"])
        self.assertEqual(sorted(item["packet"] for item in report["rejected"]),
                         ["bypass.md", "cut.md", "escape.md", "immutable.md", "partial.md"])
        self.assertEqual(report["ignored"], ["note.txt"])
        self.assertEqual((companion / "Campaign/Хроники/Арка.md").read_text(encoding="utf-8"),
                         "новый канон\n=== Глава 2 ===\nпродолжение\n")
        self.assertTrue((companion / "Campaign/Хроники/Короткий.md").is_file())
        self.assertFalse((companion / "Campaign/Хроники/Первый.md").exists())
        self.assertFalse((companion / "Campaign/Хроники/Обрезок.md").exists())
        self.assertIn("текст", (sessions / "S-20261102-1.md").read_text(encoding="utf-8"))
        self.assertEqual((sessions / "S-20261101-1.md").read_text(encoding="utf-8"), "старый\n")
        self.assertFalse((companion / "Campaign/Хроники/Новый.md").exists())
        self.assertFalse((companion / "AGENTS.md").exists())
        self.assertTrue((inbox / "Обработано" / "good-20261104T120000Z-manual.md").is_file())
        self.assertTrue((inbox / "escape.md").is_file())

        sync_chronicle(companion, self.root)
        self.assertIn("новый канон", (self.root / "Campaign/Хроники/Арка.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
