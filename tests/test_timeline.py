"""AC-12: целостность журнала, доказательства, ветки и публикация событий."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from tests.test_publish import _create_sample_snapshot
from tools.index import empty_index, save_index
from tools.io_utils import canonical, digest, read_json
from tools.publisher import publish
from tools.rendering import render_brief
from tools.timeline import (
    _writer_lock, append_event, create_session, filter_by_branch, import_events,
    load_sessions, read_timeline, resolve_timeline,
)
from tools.validation import check_privacy, validate_event, validate_publication, validate_timeline

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-11-01T12:00:00Z"


class TimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshots = self.root / "data/snapshots"
        self.ledger = self.root / "timeline/events.jsonl"
        self.index_path = self.root / "data/index.json"
        self.published = self.root / f"release-{self.root.name}"
        self.world_dir = self.root / "data/world"
        self.world_dir.mkdir(parents=True)
        self.world = read_json((ROOT / "examples/world-reference.example.json").read_bytes())
        self.world_file = self.world_dir / f"{self.world['world_ref_id']}.json"
        self.world_file.write_bytes(canonical(self.world))
        self.index = empty_index()
        for sid, parent, branch, day in (("A", None, "main", 15), ("B", "A", "main", 16), ("C", "A", "alt", 17)):
            directory = _create_sample_snapshot(self.snapshots, sid, day=day, parent_snapshot_id=parent)
            current = read_json((directory / "current.json").read_bytes())
            for character, location in zip(current["characters"], self.world["locations"]):
                character["position"] = [location[key] for key in ("x", "y", "z")]
            (directory / "current.json").write_bytes(canonical(current))
            provenance = read_json((directory / "provenance.json").read_bytes())
            provenance.update(branch_id=branch, current_sha256=digest(canonical(current)))
            (directory / "provenance.json").write_bytes(canonical(provenance))
            self.index["snapshots"][sid] = {
                "snapshot_id": sid, "parent_snapshot_id": parent, "branch_id": branch,
                "campaign_id": "fixture", "game_time": provenance["game_time"],
                "world_ref_id": self.world["world_ref_id"], "source_kind": "save",
                "source_sha256": provenance["source_sha256"], "registered_at": STAMP,
            }
            self.index["branches"][branch] = {"name": branch, "head_snapshot_id": sid, "created_at": STAMP}
        self.index_path.write_bytes(canonical(self.index))
        for name, snapshots in (("session-main", ["A", "B"]), ("session-alt", ["A", "C"]),
                                ("session-ambiguous", ["B", "C"]), ("session-root", ["A"])):
            create_session(name, snapshots, self.index, self.root, notes="План: проверить руины.\n")

    def event(self, event_id: str, status: str = "observed", **changes: Any) -> dict[str, Any]:
        sources = {
            "observed": {"kind": "snapshot", "ref": "B"},
            "confirmed": {"kind": "user_note", "ref": "sessions/session-main/notes.md"},
            "inferred": {"kind": "other", "ref": "hypotheses/unavailable.md"},
        }
        event = {
            "format": "kenshi-event/1", "event_id": event_id, "session_id": "session-main",
            "snapshot_id": "B", "recorded_at": STAMP, "game_day": 16,
            "kind": "world_state", "status": status, "summary": f"Событие {event_id}",
            "evidence": [sources[status]],
        }
        event.update(changes)
        return event

    def ingest(self, events: list[dict[str, Any]]) -> dict[str, int]:
        return import_events(events, self.ledger, index_data=self.index, root=self.root)

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        # Промежуточные locations также должны оставаться внутри временной фикстуры.
        with patch("tools.publisher.ROOT", self.root):
            return publish(
                "B", self.snapshots, self.root / "data/diffs", self.published,
                index_path=self.index_path, world_dir=self.world_dir,
                timeline_path=self.ledger, sessions_root=self.root, **kwargs,
            )

    def bundle(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in self.published.iterdir() if path.is_file()}

    def test_chain_events_follow_parent_chain_and_are_deterministic(self) -> None:
        """События строятся по цепочке родителей (пропущенный снимок B не участвует), окно ограничено, результат повторяем."""
        from tools.chain_timeline import WINDOW_EDGES, chain_events, chain_ids

        index = {"snapshots": {
            "A": {"parent_snapshot_id": None, "registered_at": "2026-11-03T01:00:00Z"},
            "B": {"parent_snapshot_id": "A", "registered_at": "2026-11-03T02:00:00Z"},
            "C": {"parent_snapshot_id": "A", "registered_at": "2026-11-03T03:00:00Z",
                  "game_time": {"day": 17, "time": "14:30"}},
        }}
        # В дельте нет ключа day (изменилось только время суток): день берётся из индекса снимка.
        diffs = {("A", "C"): {"valid": True, "world": {"time": {"to": "14:30"}, "money": {"from": 1, "to": 5, "delta": 4}},
                              "roster": {"joined": [{"name": "Новичок"}]}}}
        asked: list[tuple[str, str]] = []

        def load(parent: str, child: str):
            asked.append((parent, child))
            return diffs.get((parent, child))

        first = chain_events(index, "C", "main", load, lambda p, c: f"{p}__{c}")
        self.assertEqual(asked, [("A", "C")])
        self.assertEqual([(e["kind"], e["summary"]) for e in first],
                         [("squad_change", "В отряд вступили: Новичок"), ("state_delta", "Казна: 1 -> 5 (+4)")])
        self.assertEqual({e["game_time"] for e in first}, {"Day 17 14:30"})
        for event in first:
            validate_event(event)
        self.assertEqual(first, chain_events(index, "C", "main", load, lambda p, c: f"{p}__{c}"))

        long_chain = {"snapshots": {f"s{n}": {"parent_snapshot_id": f"s{n - 1}" if n else None} for n in range(40)}}
        self.assertEqual(len(chain_ids(long_chain, "s39")), WINDOW_EDGES + 1)

    def test_branch_switch_detection_table(self) -> None:
        """Откат времени и чужая или не совпадающая с головой родительская связь дают смену ветки; штатное продолжение нет."""
        from tools.chain_timeline import detect_branch_switch

        index = {"snapshots": {
            "P": {"branch_id": "main", "game_time": {"day": 20, "time": "10:00"}},
            "linear": {"branch_id": "main", "game_time": {"day": 21, "time": "09:00"}},
            "rollback": {"branch_id": "main", "game_time": {"day": 19, "time": "23:59"}},
        }}
        self.assertIsNone(detect_branch_switch(index, "P", "linear", "main"))  # линейное продолжение, даже если выпуски пропущены
        self.assertIsNone(detect_branch_switch(index, None, "linear", "main"))
        self.assertEqual(detect_branch_switch(index, "P", "rollback", "main")["reason"], "time_rollback")
        self.assertEqual(detect_branch_switch(index, "P", "linear", "alt")["reason"], "parent_mismatch")

    def test_fork_publication_records_branch_switch_idempotently(self) -> None:
        """Снимок ветки alt от родителя ветки main: branch_switch в манифесте и в таймлайне, повторный выпуск идентичен."""
        def publish_fork() -> dict[str, bytes]:
            with patch("tools.publisher.ROOT", self.root):
                publish("C", self.snapshots, self.root / "data/diffs", self.published, index_path=self.index_path,
                        world_dir=self.world_dir, timeline_path=self.ledger, sessions_root=self.root, branch_id="alt")
            return self.bundle()

        first = publish_fork()
        manifest = json.loads(first["manifest.json"])
        self.assertEqual((manifest["branch_switch"]["reason"], manifest["branch_switch"]["from_branch"],
                          manifest["branch_switch"]["to_branch"]), ("parent_mismatch", "main", "alt"))
        events = [json.loads(line) for line in first["timeline.jsonl"].decode("utf-8").splitlines()]
        self.assertIn("branch_switch", [event["kind"] for event in events])
        self.assertEqual(publish_fork(), first)

    def test_import_statuses_idempotency_and_atomic_batch(self) -> None:
        """Повтор и ошибочный пакет не переписывают факты и не оставляют принятого префикса пакета."""
        observed, confirmed, inferred = [self.event(status, status) for status in ("observed", "confirmed", "inferred")]
        self.ledger.parent.mkdir()
        # Нормализация прежней строки нарушила бы append-only даже при равных JSON.
        original = (json.dumps(observed, ensure_ascii=False, separators=(", ", ": ")) + "\r\n").encode("utf-8")
        self.ledger.write_bytes(original)
        self.assertEqual(self.ingest([observed, confirmed, inferred, confirmed]), {"added": 2, "unchanged": 2})
        accepted = self.ledger.read_bytes()
        self.assertTrue(accepted.startswith(original))
        self.assertEqual([event["status"] for event in read_timeline(self.ledger)], ["observed", "confirmed", "inferred"])
        self.assertEqual(self.ingest([observed, confirmed, inferred]), {"added": 0, "unchanged": 3})
        self.assertEqual(self.ledger.read_bytes(), accepted)
        for bad in (dict(observed, summary="Другой факт"), dict(confirmed, status="unknown"),
                    self.event("orphan", session_id="absent")):
            with self.subTest(event=bad["event_id"], status=bad["status"]):
                with self.assertRaises(ValueError):
                    self.ingest([self.event("must-not-be-partially-added"), bad])
                self.assertEqual(self.ledger.read_bytes(), accepted)

    def test_corruption_and_failed_replace_preserve_ledger(self) -> None:
        """Повреждённый хвост и отказ замены не теряют ранее принятые строки или выпуск."""
        self.ingest([self.event("original")])
        original = self.ledger.read_bytes()
        self.publish(generated_at=STAMP)
        published = self.bundle()
        for tail in (b'{"unfinished":', b"not-json\n", b"\n", b"{}\n"):
            with self.subTest(tail=tail):
                corrupt = original + tail
                self.ledger.write_bytes(corrupt)
                with self.assertRaises(ValueError):
                    self.ingest([self.event("new")])
                with self.assertRaises(ValueError):
                    self.publish(generated_at=STAMP)
                self.assertEqual(self.ledger.read_bytes(), corrupt)
                self.assertEqual(self.bundle(), published)
        self.ledger.write_bytes(original)
        with patch("tools.timeline.os.replace", side_effect=OSError("fixture replace failure")):
            with self.assertRaises(OSError):
                self.ingest([self.event("new")])
        self.assertEqual(self.ledger.read_bytes(), original)
        self.assertEqual(list(self.ledger.parent.glob(".events-*")), [])
        self.ingest([self.event("new")])
        self.assertTrue(self.ledger.read_bytes().startswith(original))

    def test_competing_process_cannot_write_until_lock_is_released(self) -> None:
        """Настоящий второй процесс не обходит файловый lock и успешно пишет после освобождения."""
        self.ingest([self.event("original")])
        original = self.ledger.read_bytes()
        script = """
import sys
from pathlib import Path
from tools.io_utils import read_json
from tools.timeline import append_event
root = Path(sys.argv[1])
try:
    append_event(read_json(sys.stdin.buffer.read()), root / 'timeline/events.jsonl',
                 index_data=read_json((root / 'data/index.json').read_bytes()), root=root)
except OSError:
    sys.exit(23)
"""
        def run_writer() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run([sys.executable, "-c", script, str(self.root)], input=canonical(self.event("child")),
                                  cwd=ROOT, capture_output=True, timeout=20)
        with _writer_lock(self.ledger.with_suffix(".lock")):
            result = run_writer()
            self.assertEqual(result.returncode, 23, result.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(self.ledger.read_bytes(), original)
        result = run_writer()
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual([event["event_id"] for event in read_timeline(self.ledger)], ["original", "child"])
        self.assertTrue(self.ledger.read_bytes().startswith(original))

    def test_corrections_reject_bad_graphs_and_require_confirmation(self) -> None:
        """Коррекции сохраняют оригинал; гипотеза не отменяет факт до отдельного подтверждения."""
        base = self.event("base")
        self.ingest([base])
        original = self.ledger.read_bytes()
        def correction(event_id: str, target: str, status: str = "confirmed") -> dict[str, Any]:
            return self.event(event_id, status, kind="correction", corrects_event_id=target)
        invalid = {
            "self": [correction("self", "self")],
            "missing": [correction("missing", "absent")],
            "cycle": [correction("cycle-a", "cycle-b"), correction("cycle-b", "cycle-a")],
            "conflict": [correction("left", "base"), correction("right", "base")],
        }
        for name, events in invalid.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    self.ingest(events)
                self.assertEqual(self.ledger.read_bytes(), original)
        hypothesis = correction("hypothesis", "base", "inferred")
        self.ingest([hypothesis])
        self.assertEqual(resolve_timeline(read_timeline(self.ledger)), [base])
        self.assertEqual(resolve_timeline(read_timeline(self.ledger), include_inferred=True), [hypothesis])
        brief = render_brief({}, {"valid": False}, "B", timeline=read_timeline(self.ledger))
        facts, hypotheses = brief.split("### Неподтверждённые гипотезы (inferred)")
        self.assertIn(base["summary"], facts)
        self.assertNotIn(hypothesis["summary"], facts)
        self.assertIn(hypothesis["summary"], hypotheses)
        confirmed = correction("accepted", "hypothesis")
        self.ingest([confirmed])
        self.assertEqual(resolve_timeline(read_timeline(self.ledger)), [confirmed])
        self.assertTrue(self.ledger.read_bytes().startswith(original))
        brief = render_brief({}, {"valid": False}, "B", timeline=read_timeline(self.ledger))
        self.assertIn("исправляет hypothesis", brief)
        self.assertIn("user_note: sessions/session-main/notes.md", brief)
        self.assertNotIn(base["summary"], brief)
        self.assertEqual(brief, render_brief({}, {"valid": False}, "B", timeline=read_timeline(self.ledger)))

    def test_sessions_and_ancestry_exclude_parallel_or_ambiguous_events(self) -> None:
        """A→B и A→C наследуют A; общая сессия B/C не доказывает принадлежность ни одной ветке."""
        sessions = load_sessions(self.root, self.index)
        self.assertEqual(sessions["session-main"]["snapshot_ids"], ["A", "B"])
        notes = self.root / "sessions/session-main/notes.md"
        notes_before = notes.read_bytes()
        create_session("session-main", ["B", "A"], self.index, self.root, notes="Перезаписать план")
        self.assertEqual(notes.read_bytes(), notes_before)
        for session_id, snapshots in (("../escape", ["A"]), ("/absolute", ["A"]),
                                      ("C:\\escape", ["A"]), ("A", ["A"]),
                                      ("session-main", ["C"]), ("unknown", ["missing"])):
            with self.subTest(session=session_id, snapshots=snapshots):
                with self.assertRaises(ValueError):
                    create_session(session_id, snapshots, self.index, self.root)
        events = [self.event("root", snapshot_id="A"), self.event("main"),
                  self.event("alt", snapshot_id="C", session_id="session-alt")]
        for identity, session in (("session-only-main", "session-main"), ("ambiguous", "session-ambiguous"),
                                  ("session-only-root", "session-root"), ("unbound", "missing-session")):
            event = self.event(identity, "confirmed", session_id=session)
            event.pop("snapshot_id")
            events.append(event)
        events.append(self.event("foreign-correction", "confirmed", kind="correction", corrects_event_id="alt"))
        before = copy.deepcopy(events)
        for branch, snapshot, expected in (
            ("main", "B", {"root", "main", "session-only-main", "session-only-root"}),
            ("alt", "C", {"root", "alt", "session-only-root"}),
            ("main", "A", {"root", "session-only-root"}),
        ):
            with self.subTest(branch=branch, snapshot=snapshot):
                selected = filter_by_branch(events, branch, self.index, sessions, snapshot_id=snapshot)
                self.assertEqual({event["event_id"] for event in selected}, expected)
        self.assertEqual(events, before)

    def test_evidence_type_availability_and_safe_paths(self) -> None:
        """Недоступный, чужого типа или небезопасный источник не подтверждает событие."""
        self.ingest([self.event("original")])
        original = self.ledger.read_bytes()
        for status, kind, ref in (
            ("observed", "user_note", "sessions/session-main/notes.md"),
            ("confirmed", "snapshot", "B"),
            ("observed", "snapshot", "sessions/session-main/notes.md"),
            ("confirmed", "user_note", "sessions/session-main/missing.md"),
            ("confirmed", "user_note", "sessions/session-main"),
            ("confirmed", "screenshot", "../private.png"),
            ("confirmed", "screenshot", "C:/Users/Example/private.png"),
            ("confirmed", "screenshot", "/private.png"),
        ):
            with self.subTest(status=status, kind=kind, ref=ref):
                with self.assertRaises(ValueError):
                    self.ingest([self.event("invalid", status, evidence=[{"kind": kind, "ref": ref}])])
                self.assertEqual(self.ledger.read_bytes(), original)
        self.ingest([self.event("unavailable-hypothesis", "inferred")])
        self.assertEqual([e["event_id"] for e in resolve_timeline(read_timeline(self.ledger))], ["original"])

    def test_publication_projection_sources_privacy_and_manifest(self) -> None:
        """Bundle содержит очищенные действующие факты, корректные ссылки и не принимает испорченную проекцию."""
        base = self.event("base")
        correction = self.event("corrected", "confirmed", kind="correction", corrects_event_id="base",
                                summary="Смена владельца подтверждена; файл C:\\Users\\Example\\private.png")
        correction["evidence"][0]["note"] = "Проверено по C:\\Users\\Example\\private.png"
        ambiguous = self.event("ambiguous", "confirmed", session_id="session-ambiguous")
        ambiguous.pop("snapshot_id")
        self.ingest([base, correction, self.event("visible"), self.event("guess", "inferred"),
                     self.event("parallel", snapshot_id="C", session_id="session-alt"), ambiguous])
        original = self.ledger.read_bytes()
        self.publish(generated_at=STAMP)
        manifest = read_json((self.published / "manifest.json").read_bytes())
        self.assertEqual(manifest["artifacts"]["timeline"], "timeline.jsonl")
        projected = read_timeline(self.published / "timeline.jsonl", projection=True)
        self.assertEqual({e["event_id"] for e in projected}, {"corrected", "visible"})
        self.assertEqual(next(e for e in projected if e["event_id"] == "visible")["evidence"][0]["ref"], "extended/current.json")
        brief = (self.published / "brief.md").read_text(encoding="utf-8")
        self.assertIn("исправляет base", brief)
        self.assertIn("Локальный источник; недоступен облачному читателю", brief)
        self.assertNotIn("Example", brief)
        self.assertNotIn("[inferred]", brief)
        self.assertEqual(self.ledger.read_bytes(), original)
        self.assertEqual(validate_publication(self.published, self.index)["status"], "passed")
        for name, data in self.bundle().items():
            check_privacy(data.decode("utf-8"), name)
        self.assertFalse((self.published / "sessions").exists())
        for failure in ("outside", "missing", "unregistered", "inferred", "private"):
            with self.subTest(failure=failure):
                package = self.root / f"invalid-{failure}"
                shutil.copytree(self.published, package)
                altered = copy.deepcopy(manifest)
                if failure == "outside":
                    altered["artifacts"]["timeline"] = "../timeline/events.jsonl"
                elif failure == "unregistered":
                    del altered["artifacts"]["timeline"]
                elif failure == "missing":
                    (package / "timeline.jsonl").unlink()
                else:
                    event = self.event("bad", "inferred" if failure == "inferred" else "confirmed")
                    if failure == "private":
                        event["summary"] = "Файл C:\\Users\\Example\\private.png"
                    (package / "timeline.jsonl").write_bytes(canonical(event))
                (package / "manifest.json").write_bytes(canonical(altered))
                with self.assertRaises(ValueError):
                    validate_publication(package, self.index)

    def test_only_content_changes_publication_timestamp_and_timeline_hash(self) -> None:
        """Течение времени, повтор импорта и скрытая гипотеза не создают новый фактический выпуск."""
        class Clock(datetime):
            value = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return cls.value if tz is not None else cls.value.replace(tzinfo=None)

        self.ingest([self.event("first")])
        with patch("tools.publisher.datetime", Clock), patch("tools.locator.datetime", Clock):
            self.publish()
            first = self.bundle()
            for hour, additions in ((13, [self.event("first")]), (14, [self.event("hidden", "inferred")])):
                Clock.value = datetime(2026, 11, 1, hour, tzinfo=timezone.utc)
                self.ingest(additions)
                self.publish()
                self.assertEqual(self.bundle(), first)
            self.ingest([self.event("second", "confirmed")])
            self.publish()
            second = self.bundle()
            previous, current = [read_json(bundle["manifest.json"]) for bundle in (first, second)]
            self.assertEqual(previous["snapshot_id"], current["snapshot_id"])
            self.assertNotEqual(previous["generated_at"], current["generated_at"])
            self.assertNotEqual(digest(first["timeline.jsonl"]), digest(second["timeline.jsonl"]))
            self.assertEqual(first["locations.json"], second["locations.json"])
            Clock.value = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
            self.publish()
            self.assertEqual(self.bundle(), second)

    def test_static_geography_and_location_presence_do_not_create_world_events(self) -> None:
        """in_location/near_location не доказывают зачистку; подтверждённое World State не правит географию."""
        source_before = self.world_file.read_bytes()
        self.publish(generated_at=STAMP)
        first = self.bundle()
        statuses = {c["resolution_status"] for c in read_json(first["locations.json"])["characters"]}
        self.assertEqual(statuses, {"in_location", "near_location"})
        self.assertEqual(first["timeline.jsonl"], b"")
        self.assertEqual(read_timeline(self.ledger), [])
        self.assertFalse(self.ledger.exists())
        self.ingest([self.event("world-change", "confirmed", summary="Поселение захвачено; владелец сменился")])
        self.publish(generated_at=STAMP)
        second = self.bundle()
        self.assertEqual(first["world_reference.json"], second["world_reference.json"])
        self.assertEqual(first["locations.json"], second["locations.json"])
        self.assertEqual(self.world_file.read_bytes(), source_before)
        self.assertEqual([e["event_id"] for e in read_timeline(self.ledger)], ["world-change"])
        self.assertEqual([e["event_id"] for e in read_timeline(self.published / "timeline.jsonl", projection=True)], ["world-change"])

    def test_cli_timeline_record_and_period_diff(self) -> None:
        """Проверка CLI команд timeline record и period-diff."""
        from tools.campaign import main as campaign_main

        # 1. Запись подтвержденного события через CLI timeline record
        create_session("sess-test", ["A", "B"], self.index, root=self.root)
        save_index(self.index, self.index_path)

        campaign_main([
            "timeline", "record",
            "--root", str(self.root),
            "--event-id", "ev-clean-lab",
            "--session-id", "sess-test",
            "--summary", "Зачищена древняя лаборатория в Черной Пустыне",
            "--day", "16",
            "--snapshot-id", "B",
            "--kind", "combat",
            "--status", "confirmed",
            "--evidence-kind", "user_note",
            "--evidence-ref", "sessions/sess-test/notes.md",
            "--evidence-note", "Зачистка подтверждена игроком",
        ])

        recorded_events = read_timeline(self.ledger)
        self.assertEqual(len(recorded_events), 1)
        self.assertEqual(recorded_events[0]["event_id"], "ev-clean-lab")
        self.assertEqual(recorded_events[0]["summary"], "Зачищена древняя лаборатория в Черной Пустыне")

        # 2. Публикация проецирует событие в release bundle
        self.publish(branch_id="main")
        proj_events = read_timeline(self.published / "timeline.jsonl", projection=True)
        self.assertEqual(len(proj_events), 1)
        self.assertEqual(proj_events[0]["event_id"], "ev-clean-lab")
        brief_text = (self.published / "brief.md").read_text(encoding="utf-8")
        self.assertIn("Зачищена древняя лаборатория в Черной Пустыне", brief_text)

        # 3. Вызов period-diff по дням через CLI
        out_diff = self.root / "diff_res.json"
        campaign_main([
            "diff",
            "--from-day", "15",
            "--to-day", "16",
            "--branch-id", "main",
            "--snapshots-dir", str(self.snapshots),
            "--index-file", str(self.index_path),
            "--output", str(out_diff),
        ])
        diff_output = read_json(out_diff.read_bytes())
        self.assertTrue(diff_output.get("valid"))
        self.assertEqual(diff_output.get("from", {}).get("snapshot_id"), "A")
        self.assertEqual(diff_output.get("to", {}).get("snapshot_id"), "B")


if __name__ == "__main__":
    unittest.main()
