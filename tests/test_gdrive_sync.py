"""Синхронизация с Google Диском: перезапись на месте, сбор каталога сессии и приём пакетов."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.config import find_gdrive_root
from tools.gdrive_sync import (DOCS_FILES, PIPELINE_SCOPE, PROFILE_CHRONICLE, PROFILE_GAME, InboxError, apply_packet, build_plan, ensure_buffer,
                               expand_profile, plan_prune, ingest_inbox, sync_chronicle, sync_docs, sync_drive, sync_session, write_in_place)


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

    def test_session_layout_without_json_and_images_and_missing_drive(self) -> None:
        """Ловит запись JSON и кадров .jpg на Диск (не читаются блокнотом), потерю tables/ и отказ публикации при
        неподключённом Диске."""
        published = self.tmp / "published"
        screens = published / "screenshots"
        (published / "tables").mkdir(parents=True)
        screens.mkdir()
        for name in ("brief.md", "squad.md", "changes.md", "changes.json", "timeline.jsonl", "manifest.json"):
            (published / name).write_text(name, encoding="utf-8")
        for name in ("index.md", "a.csv"):
            (published / "tables" / name).write_text(name, encoding="utf-8")
        (screens / "index.md").write_text("index", encoding="utf-8")
        (screens / "screenshots_latest.pdf").write_bytes(b"%PDF")
        (screens / "a-1.jpg").write_bytes(b"a")
        (screens / "index.json").write_text("{}", encoding="utf-8")
        (published / "current").mkdir()
        (published / "current/index.json").write_text("{}", encoding="utf-8")

        report = sync_session(published, self.root)
        self.assertEqual(report["errors"], [])
        shipped = sorted(p.relative_to(self.root / "published").as_posix() for p in (self.root / "published").rglob("*") if p.is_file())
        self.assertEqual(shipped, ["brief.md", "changes.md", "screenshots/index.md", "screenshots/screenshots_latest.pdf",
                                   "squad.md", "tables/a.csv", "tables/index.md"])
        self.assertEqual(sync_session(published, self.root)["written"], 0)

        self.assertEqual(sync_drive(self.tmp / "absent", published, None)["status"], "skipped")
        self.assertIsNone(find_gdrive_root({}, (self.tmp / "absent",)))
        self.assertEqual(find_gdrive_root({}, (self.tmp / "absent", self.root)), self.root)

    def test_profile_allowlists(self) -> None:
        """Ловит попадание JSON, .jpg, README, prompts, drafts и чужого руководства в профили и потерю общих файлов."""
        pool = [
            "published/brief.md", "published/changes.json", "published/timeline.jsonl", "published/manifest.json",
            "published/tables/index.md", "published/tables/squad.csv", "published/tables/x.json",
            "published/screenshots/index.json", "published/screenshots/a-1.jpg", "published/screenshots/feed.md",
            "Campaign/К/Kenshi_Хронология.md", "Campaign/К/Kenshi_Текущая_арка.md", "Campaign/К/Kenshi_Архетипы_и_билды.md",
            "Campaign/К/archive/Kenshi_Библия.md", "Campaign/К/archive/novel/Повесть.md", "Campaign/К/archive/sessions/S-1.md",
            "Campaign/К/Заметки.md", "README.md", "prompts/p.md", "sites/s.md", "templates/template-session.md",
            "docs/companion-guide.md", "docs/chronicler-guide.md", "docs/Kenshi_sources.md", "docs/reference/lore.md",
            "docs/reference/drafts/x.md", "docs/setup-decision.md",
        ]
        self.assertEqual(expand_profile(PROFILE_GAME, pool), sorted([
            "published/brief.md", "published/tables/index.md", "published/tables/squad.csv", "published/screenshots/feed.md",
            "Campaign/К/Kenshi_Хронология.md", "Campaign/К/Kenshi_Текущая_арка.md", "Campaign/К/Kenshi_Архетипы_и_билды.md",
            "templates/template-session.md", "docs/companion-guide.md", "docs/Kenshi_sources.md", "docs/reference/lore.md"]))
        self.assertEqual(expand_profile(PROFILE_CHRONICLE, pool), sorted([
            "Campaign/К/Kenshi_Хронология.md", "Campaign/К/archive/Kenshi_Библия.md", "Campaign/К/archive/novel/Повесть.md",
            "Campaign/К/archive/sessions/S-1.md", "docs/chronicler-guide.md", "docs/Kenshi_sources.md"]))

    def test_profile_chars_report_and_os_junk(self) -> None:
        """Ловит неверную сортировку/сумму символов (UTF-8 символы, не байты; PDF и отсутствующие вне суммы) и
        попадание desktop.ini/Thumbs.db в состав профиля и outside_set."""
        published = self.tmp / "published"
        (published / "screenshots").mkdir(parents=True)
        (published / "brief.md").write_text("абв", encoding="utf-8")
        (published / "squad.md").write_text("a" * 10, encoding="utf-8")
        (published / "desktop.ini").write_text("junk", encoding="utf-8")
        (published / "screenshots" / "screenshots_latest.pdf").write_bytes(b"%PDF")
        (self.root / "published").mkdir()
        (self.root / "published" / "Thumbs.db").write_bytes(b"x")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "desktop.ini").write_text("junk", encoding="utf-8")
        plan = build_plan(published, None, self.root)
        game = plan["profiles"]["game"]
        self.assertEqual([(i["path"], i["chars"]) for i in game["files_chars"]],
                         [("published/squad.md", 10), ("published/brief.md", 3),
                          ("published/screenshots/screenshots_latest.pdf", None)])
        self.assertEqual(game["total_chars"], 13)
        self.assertNotIn("published/desktop.ini", game["files"])
        self.assertEqual(plan_prune(self.root, plan, {}, None, ("published", "docs"))[1], [])

    def test_chronicle_mirror_and_inbox_packets(self) -> None:
        """Ловит запись вне Campaign/, частичное применение пакета и перезапись неизменяемого артефакта сессии."""
        remote = self.tmp / "remote.git"
        companion = self.tmp / "companion"
        sessions = companion / "Campaign" / "Хроники" / "archive" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "S-20261101-1.md").write_text("старый\n", encoding="utf-8")
        (companion / "Campaign" / "Хроники" / "Арка.md").write_text("канон\n", encoding="utf-8")
        (companion / "Campaign/README.md").write_text("корень\n", encoding="utf-8")
        git = lambda *args, cwd=companion: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8")
        git("init", "--bare", "-b", "main", str(remote), cwd=self.tmp)
        git("init", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        git("add", ".")
        git("commit", "-m", "init")
        git("remote", "add", "origin", str(remote))
        git("push", "-u", "origin", "main")
        ensure_buffer(self.root)
        inbox = self.root / "tmp/inbox"

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
        (inbox / "tail.md").write_text("=== ФАЙЛ: Хвост.md ===\nа\n=== КОНЕЦ ===\nоторванный хвост\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "typo.md").write_text("=== ФАЙЛ: Опечатка.md ===\nа\n=== КОНЕЦ ===\n=== ФАЙЛ Другой.md ===\nб\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "nested.md").write_text(
            "=== ВЫГРУЗКА S-20261103-1 ===\n---\ncampaign: Хроники\n---\n=== ФАЙЛ: Вложенный.md ===\nа\n=== КОНЕЦ ===\n", encoding="utf-8")
        (inbox / "note.txt").write_text("обычная заметка", encoding="utf-8")

        report = ingest_inbox(self.root, companion, now=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc))

        self.assertEqual([item["packet"] for item in report["processed"]], ["good.md", "short.md"])
        self.assertEqual(report["processed"][0]["manual"], ["ПРАВКИ: Арка.md"])
        self.assertEqual(sorted(item["packet"] for item in report["rejected"]),
                         ["bypass.md", "cut.md", "escape.md", "immutable.md", "nested.md", "partial.md", "tail.md", "typo.md"])
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
        self.assertTrue((self.root / "tmp/processed/good-20261104T120000Z-manual.md").is_file())
        self.assertTrue((self.root / "tmp/checkpoints").is_dir())
        self.assertIn("Короткий.md", git("-c", "core.quotepath=off", "--git-dir", str(remote), "show", "--name-only", "--format=", "main", cwd=self.tmp).stdout)
        self.assertTrue((inbox / "escape.md").is_file())

        # Отказ push: пакет остаётся во входящих, повтор после восстановления доводит его до processed.
        (inbox / "later.md").write_text("=== ФАЙЛ: Позже.md ===\nг\n=== КОНЕЦ ===\n", encoding="utf-8")
        for name in ("escape", "immutable", "bypass", "cut", "partial", "tail", "typo", "nested"):
            (inbox / f"{name}.md").unlink()
        git("remote", "set-url", "--push", "origin", str(self.tmp / "absent.git"))
        failed = ingest_inbox(self.root, companion)
        self.assertEqual([item["packet"] for item in failed["rejected"]], ["later.md"])
        self.assertTrue((inbox / "later.md").is_file())
        git("remote", "set-url", "--push", "origin", str(remote))
        self.assertEqual([item["packet"] for item in ingest_inbox(self.root, companion)["processed"]], ["later.md"])

        (companion / "Campaign/Хроники/Черновик.md").write_text("не зафиксировано\n", encoding="utf-8")
        sync_chronicle(companion, self.root)
        self.assertFalse((self.root / "Campaign/Хроники/Черновик.md").exists())
        self.assertFalse((self.root / "Campaign/README.md").exists())
        self.assertIn("новый канон", (self.root / "Campaign/Хроники/Арка.md").read_text(encoding="utf-8"))

    def test_sync_docs_conflict_copy_unchanged_and_repeat(self) -> None:
        """Ловит потерю ручной правки Диска (нет копии в tmp/conflicts), запись прежней версии main поверх копии и
        лишнюю запись при повторе."""
        remote, companion = self.tmp / "remote.git", self.tmp / "companion"
        (companion / "docs" / "reference").mkdir(parents=True)
        (companion / "docs" / "reference" / "drafts").mkdir()
        git = lambda *args, cwd=companion: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8")
        git("init", "--bare", "-b", "main", str(remote), cwd=self.tmp)
        git("init", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        (companion / "docs/companion-guide.md").write_text("v1\n", encoding="utf-8")
        (companion / "docs/session-protocol.md").write_text("proto\n", encoding="utf-8")
        (companion / "templates").mkdir()
        for extra in ("docs/response-formatting.md", "docs/Kenshi_sources.md", "docs/Kenshi_RU_EN.md", "templates/template-session.md"):
            (companion / extra).write_text(extra, encoding="utf-8")
        (companion / "docs/reference/lore.md").write_text("lore\n", encoding="utf-8")
        (companion / "docs/reference/drafts/x.md").write_text("draft\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "v1")
        (companion / "docs/companion-guide.md").write_text("v2\n", encoding="utf-8")
        git("commit", "-am", "v2")
        git("remote", "add", "origin", str(remote))
        git("push", "-u", "origin", "main")
        drive = self.root / "docs"
        (drive / "reference").mkdir(parents=True)
        (drive / "companion-guide.md").write_bytes(b"v1\n")       # прежняя версия main: не конфликт
        (drive / "session-protocol.md").write_bytes(b"manual\n")   # ручная правка: конфликт
        (drive / "reference/lore.md").write_bytes(b"lore\n")       # совпадает: не пишется
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        first = sync_docs(companion, self.root, now)
        self.assertEqual(first["errors"], [])
        self.assertEqual([item["path"] for item in first["conflicts"]], ["docs/session-protocol.md"])
        self.assertEqual((self.root / "tmp/conflicts/docs/session-protocol-20260102T030405Z.md").read_bytes(), b"manual\n")
        self.assertEqual(first["files_unchanged"], ["docs/reference/lore.md"])
        self.assertEqual((drive / "companion-guide.md").read_bytes(), b"v2\n")
        self.assertEqual((drive / "session-protocol.md").read_bytes(), b"proto\n")
        self.assertFalse((drive / "reference/drafts").exists())
        state = json.loads((self.root / "tmp/sync-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["origin_main"], git("rev-parse", "HEAD").stdout.strip())
        self.assertEqual(sorted(state["files"]), sorted((*DOCS_FILES, "docs/reference/lore.md")))

        (self.root / "tmp/sync-state.json").write_text(json.dumps({**state, "campaign": {"Campaign/x.md": "h"}}), encoding="utf-8")
        second = sync_docs(companion, self.root, now)
        self.assertEqual(json.loads((self.root / "tmp/sync-state.json").read_text(encoding="utf-8"))["campaign"], {"Campaign/x.md": "h"})
        self.assertEqual((second["written"], second["conflicts"], second["errors"]), (0, [], []))

    def test_campaign_drive_edits_are_protected(self) -> None:
        """Ловит перезапись правок игрока на Диске, пропуск прежней версии (в т.ч. с CRLF), удаление/запись файлов только
        на Диске и запись Campaign/ из pipeline."""
        companion = self.tmp / "companion"
        camp = companion / "Campaign" / "К"
        camp.mkdir(parents=True)
        git = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(companion), check=True, capture_output=True, text=True, encoding="utf-8")
        git("init", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        git("config", "core.autocrlf", "false")
        (camp / "A.md").write_bytes(b"a1\n")
        (camp / "B.md").write_bytes(b"b1\n")
        git("add", ".")
        git("commit", "-m", "v1")
        (camp / "A.md").write_bytes(b"a2\n")
        (camp / "B.md").write_bytes(b"b2\n")
        (camp / "C.md").write_bytes(b"c1\n")
        git("add", ".")
        git("commit", "-m", "v2")
        drive = self.root / "Campaign" / "К"
        (drive / "archive" / "sessions").mkdir(parents=True)
        (drive / "A.md").write_bytes(b"a1\r\n")                        # прежняя версия с CRLF: перезаписать
        (drive / "B.md").write_bytes("правка игрока\n".encode("utf-8"))  # ручная правка: не трогать
        (drive / "archive/sessions/S-new.md").write_bytes(b"new\n")    # только на Диске
        identity = os.stat(drive / "A.md").st_ino

        report = sync_chronicle(companion, self.root)

        self.assertEqual(report["errors"], [])
        self.assertEqual((drive / "A.md").read_bytes(), b"a2\n")
        self.assertEqual(os.stat(drive / "A.md").st_ino, identity)
        self.assertEqual((drive / "B.md").read_bytes(), "правка игрока\n".encode("utf-8"))
        self.assertEqual((drive / "C.md").read_bytes(), b"c1\n")
        self.assertEqual(report["modified_on_drive"], ["Campaign/К/B.md"])
        self.assertEqual(report["only_on_drive"], ["Campaign/К/archive/sessions/S-new.md"])
        self.assertEqual((drive / "archive/sessions/S-new.md").read_bytes(), b"new\n")
        state = json.loads((self.root / "tmp/sync-state.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["campaign"]), ["Campaign/К/A.md", "Campaign/К/C.md"])

        # Правка Диска поверх записанной версии: файл защищён.
        (drive / "C.md").write_bytes(b"c-edit\n")
        (camp / "C.md").write_bytes(b"c2\n")
        git("commit", "-am", "v3")
        self.assertEqual(sync_chronicle(companion, self.root)["modified_on_drive"], ["Campaign/К/B.md", "Campaign/К/C.md"])

        # pipeline (sync_docs подменён: ему нужен origin) Campaign/ не пишет.
        (drive / "A.md").write_bytes(b"a1\n")
        with mock.patch("tools.gdrive_sync.sync_docs", return_value={"errors": []}) as docs:
            result = sync_drive(self.root, self.tmp / "published", companion, scope=PIPELINE_SCOPE)
        self.assertNotIn("chronicle", result)
        self.assertEqual(result["status"], "synced")
        docs.assert_called_once()
        self.assertEqual((drive / "A.md").read_bytes(), b"a1\n")

    def test_dry_run_prune_and_repeat(self) -> None:
        """Ловит запись при dry-run, удаление файлов вне репозитория и Campaign/, удаление без --prune и лишнюю запись
        при повторе."""
        remote, companion, published = self.tmp / "remote.git", self.tmp / "companion", self.tmp / "published"
        git = lambda *args, cwd=companion: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8")
        for name, text in {"Campaign/К/Kenshi_Хронология.md": "хр\n", "Campaign/К/Лишнее.md": "в репозитории, не в наборе\n",
                           "Campaign/К/archive/sessions/S-1.md": "s\n", "docs/companion-guide.md": "g\n",
                           "docs/chronicler-guide.md": "c\n", "docs/old-note.md": "устарел\n", "prompts/p.md": "p\n"}.items():
            (companion / name).parent.mkdir(parents=True, exist_ok=True)
            (companion / name).write_text(text, encoding="utf-8")
        git("init", "--bare", "-b", "main", str(remote), cwd=self.tmp)
        git("init", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        git("add", ".")
        git("commit", "-m", "v1")
        git("remote", "add", "origin", str(remote))
        git("push", "-u", "origin", "main")
        (published / "tables").mkdir(parents=True)
        (published / "screenshots").mkdir()
        for name in ("brief.md", "changes.md", "squad.md", "changes.json", "manifest.json", "tables/index.md", "tables/a.csv",
                     "screenshots/index.md", "screenshots/index.json", "screenshots/a.jpg"):
            (published / name).write_text(name, encoding="utf-8")
        archive = self.tmp / "archive"
        archive.mkdir()
        (archive / "old-frame.jpg").write_bytes(b"j")

        def put(relative: str, data: bytes = b"x") -> None:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_bytes(data)

        for relative in ("published/changes.json", "published/screenshots/a.jpg", "published/screenshots/old-frame.jpg",
                         "docs/old-note.md", "docs/prompts-copy.txt", "published/manual.txt", "docs/hand.gdoc", "tmp/inbox/p.md"):
            put(relative)
        put("Campaign/К/Лишнее.md", "правка игрока\n".encode("utf-8"))
        put("Campaign/К/archive/sessions/S-drive.md")
        fell_out = ["docs/old-note.md", "published/changes.json", "published/screenshots/a.jpg", "published/screenshots/old-frame.jpg"]
        outside = ["docs/hand.gdoc", "docs/prompts-copy.txt", "published/manual.txt"]

        def tree() -> dict[str, bytes]:
            return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}

        def run(**kwargs):
            return sync_drive(self.root, published, companion, archive_dir=archive, **kwargs)

        before = tree()
        dry = run(dry_run=True, prune=True)
        self.assertEqual(tree(), before)
        self.assertFalse((self.root / "tmp/sync-state.json").exists())
        self.assertIn("create published/brief.md", dry["summary"]["planned"])
        self.assertIn("create docs/chronicler-guide.md", dry["summary"]["planned"])
        self.assertEqual([item for item in dry["summary"]["planned"] if item.startswith("delete")], [f"delete {p}" for p in fell_out])
        self.assertEqual((dry["summary"]["written"], dry["summary"]["deleted"]), (0, []))

        plain = run()
        self.assertEqual(plain["status"], "synced")
        self.assertEqual(plain["summary"]["to_delete"], fell_out)
        self.assertEqual(plain["summary"]["outside_set"], outside)
        self.assertEqual(plain["summary"]["deleted"], [])
        self.assertTrue(all((self.root / p).is_file() for p in fell_out))
        shipped = sorted(p for p in tree() if p.startswith("published/") and p not in fell_out and p != "published/manual.txt")
        self.assertEqual(shipped, ["published/brief.md", "published/changes.md", "published/screenshots/index.md",
                                   "published/squad.md", "published/tables/a.csv", "published/tables/index.md"])
        # --scope all (по умолчанию) Campaign/ не пишет: ни создания, ни правки, ни удаления.
        self.assertFalse((self.root / "Campaign/К/Kenshi_Хронология.md").exists())
        self.assertFalse((self.root / "prompts").exists())
        self.assertEqual(plain["profiles"]["chronicle"]["files"], [
            "Campaign/К/Kenshi_Хронология.md", "Campaign/К/archive/sessions/S-1.md", "Campaign/К/archive/sessions/S-drive.md",
            "docs/chronicler-guide.md"])

        pruned = run(prune=True)
        self.assertEqual(pruned["summary"]["deleted"], fell_out)
        self.assertEqual(pruned["summary"]["outside_set"], outside)
        self.assertTrue(all(not (self.root / p).exists() for p in fell_out))
        self.assertTrue(all((self.root / p).exists() for p in outside))
        self.assertEqual((self.root / "Campaign/К/Лишнее.md").read_bytes(), "правка игрока\n".encode("utf-8"))
        self.assertTrue((self.root / "Campaign/К/archive/sessions/S-drive.md").is_file())

        settled = tree()
        again = run(prune=True)
        self.assertEqual((again["summary"]["written"], again["summary"]["deleted"], again["status"]), (0, [], "synced"))
        self.assertEqual(tree(), settled)

        # Campaign/ пишется только явным chronicle; правка игрока при этом сохраняется.
        explicit = run(scope="chronicle")
        self.assertEqual(explicit["status"], "synced")
        self.assertEqual((self.root / "Campaign/К/Kenshi_Хронология.md").read_text(encoding="utf-8"), "хр\n")
        self.assertEqual((self.root / "Campaign/К/Лишнее.md").read_bytes(), "правка игрока\n".encode("utf-8"))

    def test_inbox_rejects_docs_prompts_templates(self) -> None:
        """Ловит запись пакетом в docs/, prompts/, templates/ (источник - main) через префикс каталога кампании."""
        companion = self.tmp / "companion"
        (companion / "Campaign" / "К").mkdir(parents=True)
        for directory in ("docs", "prompts", "templates"):
            packet = self.tmp / f"{directory}.md"
            packet.write_text(f"=== ФАЙЛ: {directory}/x.md ===\nа\n=== КОНЕЦ ===\n", encoding="utf-8")
            with self.assertRaisesRegex(InboxError, f"{directory}/ не принимается"):
                apply_packet(packet, companion)


if __name__ == "__main__":
    unittest.main()
