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

from tests.test_gdrive_native import FakeGoogle, make_native
from tools.config import find_gdrive_root
from tools.gdrive_native import DOC_MIME
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

        fake = FakeGoogle()
        native = make_native(fake, self.root)
        report = sync_session(published, self.root, native=native)
        self.assertEqual(report["errors"], [])
        # md/csv - только нативные файлы Google; на смонтированном Диске остаётся один PDF.
        shipped = sorted(p.relative_to(self.root / "published").as_posix() for p in (self.root / "published").rglob("*") if p.is_file())
        self.assertEqual(shipped, ["screenshots/screenshots_latest.pdf"])
        self.assertEqual(sorted(fake.tree()), ["published/brief", "published/changes", "published/screenshots/index",
                                               "published/squad", "published/tables/a", "published/tables/index"])
        self.assertEqual(sync_session(published, self.root, native=native)["written"], 0)

        self.assertEqual(sync_drive(self.tmp / "absent", published, None)["status"], "skipped")
        self.assertIsNone(find_gdrive_root({}, (self.tmp / "absent",)))
        self.assertEqual(find_gdrive_root({}, (self.tmp / "absent", self.root)), self.root)

    def test_profile_allowlists(self) -> None:
        """Ловит попадание JSON, .jpg, README, prompts, drafts, чужого руководства и семи таблиц справочника юнитов
        в профили и потерю общих файлов."""
        pool = [
            "published/brief.md", "published/changes.json", "published/timeline.jsonl", "published/manifest.json",
            "published/tables/index.md", "published/tables/squad.csv", "published/tables/x.json",
            "published/tables/unit_templates.csv", "published/tables/unit_template_gear.csv",
            "published/tables/squad_templates.csv", "published/tables/squad_members.csv",
            "published/tables/squad_spawns.csv", "published/tables/faction_relations.csv",
            "published/tables/faction_hostility.csv",
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
        fake = FakeGoogle()
        sync_chronicle(companion, self.root, native=make_native(fake, self.root))
        self.assertNotIn("Campaign/Хроники/Черновик", fake.tree())
        self.assertNotIn("Campaign/README", fake.tree())
        self.assertIn("новый канон", fake.items[fake.find("Campaign/Хроники/Арка", DOC_MIME)]["text"])

    def test_sync_docs_conflict_copy_unchanged_and_repeat(self) -> None:
        """Ловит потерю правки Диска (нет копии в tmp/conflicts), запись нетронутых файлов при повторе и потерю чужих
        секций sync-state."""
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
        git("remote", "add", "origin", str(remote))
        git("push", "-u", "origin", "main")
        fake = FakeGoogle()
        native = make_native(fake, self.root)
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        first = sync_docs(companion, self.root, now, native=native)
        self.assertEqual((first["errors"], first["conflicts"]), ([], []))
        self.assertEqual(sorted(first["files_written"]), sorted((*DOCS_FILES, "docs/reference/lore.md")))
        self.assertEqual(sorted(fake.tree()), sorted(name.removesuffix(".md") for name in (*DOCS_FILES, "docs/reference/lore.md")))
        self.assertFalse((self.root / "docs/companion-guide.md").exists())

        (companion / "docs/companion-guide.md").write_text("v2\n", encoding="utf-8")
        git("commit", "-am", "v2")
        git("push")
        fake.user_edit(fake.find("docs/session-protocol", DOC_MIME), "manual\n")  # правка на Диске: конфликт
        state_path = self.root / "tmp/sync-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state_path.write_text(json.dumps({**state, "campaign": {"Campaign/x.md": "h"}}), encoding="utf-8")

        second = sync_docs(companion, self.root, now, native=native)
        self.assertEqual(second["errors"], [])
        self.assertEqual([item["path"] for item in second["conflicts"]], ["docs/session-protocol.md"])
        self.assertEqual((self.root / "tmp/conflicts/docs/session-protocol-20260102T030405Z.md").read_bytes(), b"manual\n")
        self.assertEqual(sorted(second["files_written"]), ["docs/companion-guide.md", "docs/session-protocol.md"])
        self.assertEqual(fake.items[fake.find("docs/companion-guide", DOC_MIME)]["text"], "v2\n")
        self.assertEqual(fake.items[fake.find("docs/session-protocol", DOC_MIME)]["text"], "proto\n")
        self.assertNotIn("docs/reference/drafts/x", fake.tree())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["origin_main"], git("rev-parse", "HEAD").stdout.strip())
        self.assertEqual(sorted(state["files"]), sorted((*DOCS_FILES, "docs/reference/lore.md")))
        self.assertEqual(state["campaign"], {"Campaign/x.md": "h"})
        self.assertEqual(sorted(state["native"]), sorted((*DOCS_FILES, "docs/reference/lore.md")))

        third = sync_docs(companion, self.root, now, native=native)
        self.assertEqual((third["written"], third["conflicts"], third["errors"]), (0, [], []))

    def test_campaign_is_copy_of_main(self) -> None:
        """Ловит прежнее правило «Диск важнее» (правка игрока сохраняется, а main не побеждает), удаление файлов только
        на Диске, запись правок мимо tmp/conflicts и пропуск Campaign/ в конвейере."""
        companion = self.tmp / "companion"
        camp = companion / "Campaign" / "К"
        camp.mkdir(parents=True)
        git = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(companion), check=True, capture_output=True, text=True, encoding="utf-8")
        git("init", "-b", "main")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        git("config", "core.autocrlf", "false")
        (camp / "Kenshi_Хронология.md").write_bytes(b"h1\n")
        (camp / "B.md").write_bytes(b"b1\n")
        (camp / "C.md").write_bytes(b"c1\n")
        git("add", ".")
        git("commit", "-m", "v1")
        drive = self.root / "Campaign" / "К"
        (drive / "archive" / "sessions").mkdir(parents=True)
        (drive / "archive/sessions/S-new.md").write_bytes(b"new\n")   # только на Диске (старый файл)
        (drive / "S-gem.gdoc").write_bytes(b"{}")                      # ярлык нативного Doc, созданного Gemini
        fake = FakeGoogle()
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        report = sync_chronicle(companion, self.root, native=make_native(fake, self.root), now=now)
        self.assertEqual(report["errors"], [])
        self.assertEqual(sorted(fake.tree()), ["Campaign/К/B", "Campaign/К/C", "Campaign/К/Kenshi_Хронология"])
        self.assertEqual(report["only_on_drive"], ["Campaign/К/S-gem.md", "Campaign/К/archive/sessions/S-new.md"])

        # Правка игрока на Диске: копия в tmp/conflicts и перезапись версией из main; незакоммиченный файл пропущен.
        native = make_native(fake, self.root, json.loads((self.root / "tmp/sync-state.json").read_text(encoding="utf-8"))["native"])
        identity = fake.find("Campaign/К/B", DOC_MIME)
        fake.user_edit(identity, "правка игрока\n")
        (camp / "C.md").write_bytes(b"c-uncommitted\n")
        again = sync_chronicle(companion, self.root, native=native, now=now)
        self.assertEqual(again["skipped_uncommitted"], ["C.md"])
        self.assertEqual(again["conflicts"], [{"path": "Campaign/К/B.md", "copy": "tmp/conflicts/Campaign/К/B-20260102T030405Z.md"}])
        self.assertEqual((self.root / "tmp/conflicts/Campaign/К/B-20260102T030405Z.md").read_bytes(), "правка игрока\n".encode("utf-8"))
        self.assertEqual((fake.find("Campaign/К/B", DOC_MIME), fake.items[identity]["text"]), (identity, "b1\n"))
        self.assertEqual(fake.items[fake.find("Campaign/К/C", DOC_MIME)]["text"], "c1\n")
        self.assertEqual((drive / "archive/sessions/S-new.md").read_bytes(), b"new\n")

        # Конвейер (--scope all) синхронизирует и Campaign/; sync_docs подменён: ему нужен origin.
        (camp / "C.md").write_bytes(b"c1\n")
        (camp / "Kenshi_Хронология.md").write_bytes(b"h2\n")
        git("commit", "-am", "v2")
        with mock.patch("tools.gdrive_sync.sync_docs", return_value={"errors": []}) as docs:
            result = sync_drive(self.root, self.tmp / "published", companion, scope=PIPELINE_SCOPE, api=fake.api())
        docs.assert_called_once()
        self.assertEqual((result["status"], result["chronicle"]["written"]), ("synced", 1))
        self.assertEqual(fake.items[fake.find("Campaign/К/Kenshi_Хронология", DOC_MIME)]["text"], "h2\n")

    def test_dry_run_prune_and_repeat(self) -> None:
        """Ловит запись при dry-run, удаление файлов вне репозитория и Campaign/, удаление без --prune, удаление прежнего
        .md/.csv без нативной копии и лишнюю запись при повторе."""
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
        fake = FakeGoogle()

        def put(relative: str, data: bytes = b"x") -> None:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_bytes(data)

        for relative in ("published/changes.json", "published/screenshots/a.jpg", "published/screenshots/old-frame.jpg",
                         "docs/old-note.md", "docs/prompts-copy.txt", "published/manual.txt", "docs/hand.gdoc", "tmp/inbox/p.md"):
            put(relative)
        put("Campaign/К/Лишнее.md", "правка игрока\n".encode("utf-8"))
        put("Campaign/К/archive/sessions/S-drive.md")
        # Прежние смонтированные копии файлов репозитория (наборы профилей) и ярлык нативного Doc.
        legacy = ["Campaign/К/Kenshi_Хронология.md", "docs/companion-guide.md", "published/brief.md", "published/tables/a.csv"]
        for relative in legacy:
            put(relative)
        put("published/brief.gdoc")
        fell_out = ["docs/old-note.md", "published/changes.json", "published/screenshots/a.jpg", "published/screenshots/old-frame.jpg"]
        outside = ["Campaign/К/Лишнее.md", "docs/hand.gdoc", "docs/prompts-copy.txt", "published/manual.txt"]

        def tree() -> dict[str, bytes]:
            return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}

        def run(**kwargs):
            return sync_drive(self.root, published, companion, archive_dir=archive, api=fake.api(), **kwargs)

        before = tree()
        dry = run(dry_run=True, prune=True)
        self.assertEqual(tree(), before)
        self.assertFalse((self.root / "tmp/sync-state.json").exists())
        self.assertEqual(fake.mutations, [])
        self.assertIn("create published/brief.md", dry["summary"]["planned"])
        self.assertIn("create docs/chronicler-guide.md", dry["summary"]["planned"])
        self.assertIn("create Campaign/К/Kenshi_Хронология.md", dry["summary"]["planned"])
        # Нативных копий ещё нет: прежние файлы не к удалению.
        self.assertEqual([item for item in dry["summary"]["planned"] if item.startswith("delete")], [f"delete {p}" for p in fell_out])
        self.assertEqual(sorted(dry["summary"]["legacy_waiting_native"]), sorted(legacy))
        self.assertEqual((dry["summary"]["written"], dry["summary"]["deleted"]), (0, []))

        plain = run()
        self.assertEqual(plain["status"], "synced")
        self.assertEqual(plain["summary"]["to_delete"], fell_out)
        self.assertEqual(plain["summary"]["outside_set"], outside)
        self.assertEqual(plain["summary"]["deleted"], [])
        self.assertEqual(plain["summary"]["legacy_to_delete"], sorted(legacy))
        self.assertEqual(plain["summary"]["only_on_drive"], ["Campaign/К/archive/sessions/S-drive.md"])
        self.assertTrue(all((self.root / p).is_file() for p in [*fell_out, *legacy]))
        self.assertEqual(sorted(fake.tree()), sorted([
            "Campaign/К/Kenshi_Хронология", "Campaign/К/archive/sessions/S-1", "docs/chronicler-guide", "docs/companion-guide",
            "published/brief", "published/changes", "published/squad", "published/screenshots/index", "published/tables/a",
            "published/tables/index"]))
        self.assertFalse((self.root / "prompts").exists())
        self.assertEqual(plain["profiles"]["chronicle"]["files"], [
            "Campaign/К/Kenshi_Хронология.md", "Campaign/К/archive/sessions/S-1.md", "Campaign/К/archive/sessions/S-drive.md",
            "docs/chronicler-guide.md"])

        mutations = list(fake.mutations)
        before = tree()
        planned = run(dry_run=True, prune=True)
        self.assertEqual((tree(), fake.mutations), (before, mutations))
        self.assertEqual(sorted(item for item in planned["summary"]["planned"] if item.startswith("delete")),
                         sorted(f"delete {p}" for p in [*fell_out, *legacy]))

        pruned = run(prune=True)
        self.assertEqual(sorted(pruned["summary"]["deleted"]), sorted([*fell_out, *legacy]))
        self.assertEqual(pruned["summary"]["outside_set"], outside)
        self.assertTrue(all(not (self.root / p).exists() for p in [*fell_out, *legacy]))
        self.assertTrue(all((self.root / p).exists() for p in outside))
        self.assertEqual((self.root / "Campaign/К/Лишнее.md").read_bytes(), "правка игрока\n".encode("utf-8"))
        self.assertTrue((self.root / "Campaign/К/archive/sessions/S-drive.md").is_file())

        settled = tree()
        again = run(prune=True)
        self.assertEqual((again["summary"]["written"], again["summary"]["deleted"], again["status"]), (0, [], "synced"))
        self.assertEqual(tree(), settled)

        # Отказ записи нативной копии (лимит Doc) оставляет прежний файл даже с --prune.
        (published / "squad.md").write_text("x" * 1_500_001, encoding="utf-8")
        put("published/squad.md", b"old")
        refused = run(prune=True)
        self.assertEqual((refused["status"], refused["summary"]["deleted"]), ("partial", []))
        self.assertEqual((self.root / "published/squad.md").read_bytes(), b"old")

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
