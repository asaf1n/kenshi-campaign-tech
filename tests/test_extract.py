"""Изолированные сценарии источника, защиты записи и воспроизводимости."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.check_converter import record
from tools.config import Config
from tools.extraction import ROOT, convert_save, extract
from tools.io_utils import ExtractionError, canonical, digest, file_hash, read_json, tree_hashes
from tools.normalization import normalize

FACTION = "204-gamedata.base"


def session() -> dict:
    return {"format": "kenshi-session/10", "save": {"path": "C:/Users/artem/private", "saved_at": "mtime"},
            "world": {"day": 12, "time": "03:15", "money": 1234},
            "characters": [{"id": "ps:2", "name": "Бык", "squad_id": "b", "skills_base": {"strength": 12.123456789012345}},
                           {"id": "ps:1", "name": "Бык", "squad_id": "a"}],
            "squads": [{"squad_id": "a", "members": ["Бык"]}, {"squad_id": "b", "members": ["Бык"]}],
            "warnings": [], "meta": {"player_faction": {"sid": FACTION}, "exported_at": "clock",
                                     "timings_ms": {"total": 12}, "game_data": {"cache": "hit"}}}


class ExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="kenshi-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config((self.root / "saves",),
                             self.root / "game", self.root / "workshop", FACTION)
        self.output = self.root / "snapshots"
        self.source = self.root / "export.json"
        self.source.write_bytes(canonical(session()))

    def make_save(self, name: str = "one") -> Path:
        data_dir = self.config.game / "data"
        if not data_dir.exists():
            data_dir.mkdir(parents=True)
            (self.config.game / "locale/ru_RU").mkdir(parents=True)
            (self.config.game / "locale/ru_RU/gamedata.po").write_bytes(b"")
            for filename in ("gamedata.base", "Newwworld.mod", "Dialogue.mod", "rebirth.mod"):
                (data_dir / filename).write_bytes(struct.pack("<iiii", 17, 0, 0, 0))
        save = self.config.save_dirs[0] / name
        (save / "platoon").mkdir(parents=True)
        state = record(56, "world", {"time day": 12, "time hour": 3, "time minute": 15,
                                      "player money": 1234, "members": 0, "squads": 1}, {})
        squad = record(34, "a", {"squad index": 0}, {"platoon stringID": "a", "faction stringID": self.config.faction_sid})
        (save / "quick.save").write_bytes(struct.pack("<iii", 15, 2, 2) + state + squad)
        (save / "platoon/a.platoon").write_bytes(struct.pack("<iii", 15, 0, 0))
        return save

    def test_config_and_faction_identity(self) -> None:
        env = {"KENSHI_FACTION_SID": "env-faction", "KENSHI_SAVE_DIRS": "first;second"}
        self.assertEqual(Config.from_env(environ={}).faction_sid, FACTION)
        self.assertEqual(Config.from_env(environ=env).faction_sid, "env-faction")
        self.assertEqual(Config.from_env("cli-faction", env).faction_sid, "cli-faction")
        self.assertEqual(Config.from_env(environ=env).save_dirs, (Path("first"), Path("second")))
        save = self.make_save()
        ids = []
        for faction in (FACTION, "env-faction", "cli-faction"):
            config = Config(self.config.save_dirs, self.config.game, self.config.workshop, faction)
            state = record(56, "world", {"time day": 12, "time hour": 3, "time minute": 15,
                                          "player money": 1234, "members": 0, "squads": 1}, {})
            squad = record(34, "a", {"squad index": 0}, {"platoon stringID": "a", "faction stringID": faction})
            (save / "quick.save").write_bytes(struct.pack("<iii", 15, 2, 2) + state + squad)
            result = extract(save, "save", config, self.output)
            provenance = read_json((self.output / result["snapshot_id"] / "provenance.json").read_bytes())
            current = read_json((self.output / result["snapshot_id"] / "current.json").read_bytes())
            self.assertEqual(current["meta"]["player_faction"]["sid"], faction)
            self.assertEqual(provenance["faction_sid"], faction)
            ids.append(result["snapshot_id"])
        self.assertEqual(len(set(ids)), 3)

    def test_normalization_precision_privacy_and_optional_data(self) -> None:
        value = session()
        value["changes"] = {"history": ["C:\\Users\\artem\\history", "token=hidden"]}
        value["unknown_mod_field"] = {"value": 0.12345678901234567}
        value["meta"]["nested"] = {"password": "hidden", "trace": "failed C:\\Users\\artem\\my folder\\x.json", "url": "https://name:hidden@example.test"}
        value["warnings"] = ["quick.save: squads=5, а в .platoon найдено 4", "player_towns: zone/ неполно", "token=hidden /home/person/private"]
        a, diagnostics = normalize(value)
        other = copy.deepcopy(value)
        other["meta"]["exported_at"] = "tomorrow"
        other["meta"]["timings_ms"] = {"total": 90}
        other["meta"]["game_data"]["cache"] = "rebuilt"
        other["characters"].reverse()
        other["squads"].reverse()
        other["changes"] = {"arbitrary": 23}
        self.assertEqual(canonical(a), canonical(normalize(other)[0]))
        self.assertEqual(a["characters"][1]["skills_base"]["strength"], 12.123456789012345)
        self.assertEqual(a["unknown_mod_field"], value["unknown_mod_field"])
        text = canonical(a).decode()
        for forbidden in ("Users", "artem", "hidden", "/home/person", '"changes"', '"cache"', '"exported_at"'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(diagnostics["categories"]["property"], "incomplete")
        self.assertEqual(diagnostics["categories"]["quick_save_counts"], "inconsistent")
        self.assertEqual(diagnostics["categories"]["research"], "unavailable")
        self.assertEqual(len(a["warnings"]), 3)
        unavailable = session()
        unavailable["research"] = {}
        unavailable["warnings"] = ["quick.save: запись исследований (тип 21) не найдена; research недоступен"]
        absent = normalize(unavailable)[1]["categories"]
        self.assertEqual(absent["research"], "unavailable")
        self.assertNotIn("quick_save_counts", absent)
        for identity in ("h:1:2", "Бык", None):
            value["characters"][0]["id"] = identity
            self.assertTrue(normalize(value)[1]["limitations"])

    def test_export_no_converter_and_immutable_collision(self) -> None:
        before = file_hash(self.source)
        with patch("tools.extraction.convert_save", side_effect=AssertionError("converter called")):
            first = extract(self.source, "export", self.config, self.output)
            files = tree_hashes(self.output)
            second = extract(self.source, "export", self.config, self.output)
            self.assertEqual(first, second)
            self.assertEqual(files, tree_hashes(self.output))
            self.assertEqual(before, first["source_sha256"])
            self.assertEqual(before, file_hash(self.source))
            target = self.output / first["snapshot_id"] / "current.json"
            target.write_bytes(b"conflict")
            with self.assertRaisesRegex(ExtractionError, "другое содержимое"):
                extract(self.source, "export", self.config, self.output)
            self.assertEqual(target.read_bytes(), b"conflict")

    def test_invalid_structures_and_duplicate_ids(self) -> None:
        cases = []
        for key in ("characters", "squads", "world", "meta", "warnings", "save"):
            value = session()
            del value[key]
            cases.append(value)
        for squad in ("a", "b"):
            value = session()
            value["characters"][1].update(id="ps:2", squad_id=squad)
            cases.append(value)
        cases += [session() | {"format": "kenshi-session/99"}, session() | {"characters": [5]}]
        for value in cases:
            with self.subTest(value=value):
                self.source.write_bytes(canonical(value))
                with self.assertRaises(ExtractionError):
                    extract(self.source, "export", self.config, self.output)
                self.assertFalse(self.output.exists())
        for raw in (b'{"x":1,"x":2}', b'{"value":NaN}'):
            with self.assertRaises(ExtractionError):
                read_json(raw)

    def test_save_hashes_repeat_converter_and_mod_fingerprints(self) -> None:
        save = self.make_save()
        before = tree_hashes(save)
        protected = tree_hashes(self.config.game)
        first = extract(save, "save", self.config, self.output, "campaign", "main")
        self.assertEqual(before, tree_hashes(save))
        self.assertEqual(protected, tree_hashes(self.config.game))
        files = tree_hashes(self.output)
        self.assertEqual(first, extract(save, "save", self.config, self.output, "campaign", "main"))
        self.assertEqual(files, tree_hashes(self.output))
        meta = read_json((self.output / first["snapshot_id"] / "provenance.json").read_bytes())
        self.assertEqual(meta["source_files"], before)
        self.assertEqual(meta["source_sha256"], digest(canonical(before)))
        self.assertEqual(meta["converter"]["sha256"], digest(canonical(meta["converter"]["files"])))
        from tools.converter_worker import ENGINE_FILES
        self.assertEqual([p[0] for p in meta["converter"]["files"]], ENGINE_FILES)
        (self.config.game / "data/Newwworld.mod").write_bytes(struct.pack("<iiii", 17, 0, 0, 0) + b"mod-v2")
        mod_changed = extract(save, "save", self.config, self.output, "campaign", "main")
        self.assertNotEqual(first["snapshot_id"], mod_changed["snapshot_id"])
        mock_hashes = {k: "changed" for k in ENGINE_FILES}
        with patch("tools.extraction.engine_hashes", return_value=mock_hashes):
            engine_changed = extract(save, "save", self.config, self.output, "campaign", "main")
        self.assertNotEqual(mod_changed["snapshot_id"], engine_changed["snapshot_id"])
        self.assertNotEqual(engine_changed["snapshot_id"], extract(save, "save", self.config, self.output, "campaign", "branch")["snapshot_id"])

    def test_save_missing_corrupt_and_source_changes(self) -> None:
        save = self.make_save()
        quick = (save / "quick.save").read_bytes()
        platoon = (save / "platoon/a.platoon").read_bytes()
        for filename, bad in (("quick.save", None), ("quick.save", b"broken"), ("platoon/a.platoon", None), ("platoon/a.platoon", b"broken")):
            path = save / filename
            if bad is None:
                path.unlink()
            else:
                path.write_bytes(bad)
            with self.subTest(filename=filename, bad=bad), self.assertRaises(ExtractionError):
                extract(save, "save", self.config, self.output)
            path.write_bytes(quick if filename == "quick.save" else platoon)
            self.assertFalse(list(self.output.glob("sha256-*")))
        for mutation in ("add", "remove", "bytes"):
            def changed(*args: object, **kwargs: object) -> dict:
                result = convert_save(*args, **kwargs)
                if mutation == "add":
                    (save / "extra").write_bytes(b"new")
                elif mutation == "remove":
                    (save / "extra").unlink()
                else:
                    (save / "platoon/a.platoon").write_bytes(b"changed")
                return result
            with self.subTest(mutation=mutation), patch("tools.extraction.convert_save", side_effect=changed):
                with self.assertRaisesRegex(ExtractionError, "Источник изменился"):
                    extract(save, "save", self.config, self.output)
            self.assertFalse(list(self.output.glob("sha256-*")))

    def test_write_guard_and_output_boundaries(self) -> None:
        save = self.make_save()
        targets = [(save / "platoon/a.platoon", mode) for mode in ("w", "a", "os_open", "unlink", "rename")]
        targets += [(self.config.game / "data/gamedata.base", "w")]
        for target, mode in targets:
            before = tree_hashes(save), tree_hashes(self.config.game)
            script = (
                "import sys, os\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, sys.argv[4])\n"
                "from tools.io_utils import install_write_guard\n"
                "workspace = Path(sys.argv[1])\n"
                "target = Path(sys.argv[2])\n"
                "mode = sys.argv[3]\n"
                "denied = install_write_guard(workspace)\n"
                "try:\n"
                "    if mode == 'unlink': target.unlink()\n"
                "    elif mode == 'rename': target.rename(target.with_suffix('.moved'))\n"
                "    elif mode == 'os_open': os.close(os.open(target, os.O_WRONLY | os.O_TRUNC))\n"
                "    else:\n"
                "        with target.open(mode) as f: f.write('forbidden')\n"
                "except PermissionError:\n"
                "    pass\n"
                "if denied:\n"
                "    print('DENIED_CAUGHT')\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(self.output), str(target), mode, str(ROOT)],
                capture_output=True,
                cwd=ROOT,
            )
            with self.subTest(mode=mode, target=target):
                self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
                self.assertIn("DENIED_CAUGHT", completed.stdout.decode("utf-8", errors="replace"))
            self.assertEqual(before, (tree_hashes(save), tree_hashes(self.config.game)))
            self.assertFalse(list(self.output.glob("sha256-*")))
        for output in (save / "out", self.config.game / "out", self.root):
            with self.subTest(output=output), self.assertRaisesRegex(ExtractionError, "пересекается"):
                extract(self.source, "export", self.config, output)
        if os.name == "nt":
            alias = self.root / "alias"
            command = f"New-Item -ItemType Junction -Path '{alias}' -Target '{save}' | Out-Null"
            completed = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            try:
                with self.assertRaisesRegex(ExtractionError, "пересекается"):
                    extract(self.source, "export", self.config, alias / "out")
            finally:
                alias.rmdir()

    def test_cli_hashseed_save_and_export(self) -> None:
        save = self.make_save()
        env = os.environ | {"KENSHI_SAVE_DIRS": str(self.config.save_dirs[0]), "KENSHI_DIR": str(self.config.game),
                            "KENSHI_WORKSHOP_DIR": str(self.config.workshop), "KENSHI_FACTION_SID": FACTION}
        for kind, source in (("save", save), ("export", self.source)):
            results = []
            for seed in ("1", "42"):
                output = self.root / f"hashseed-{kind}-{seed}"
                command = [sys.executable, "-B", "-m", "tools.campaign", "extract", "--source-kind", kind,
                           "--source", str(source), "--output", str(output), "--campaign-id", "test", "--branch-id", "main"]
                completed = subprocess.run(command, env=env | {"PYTHONHASHSEED": seed}, cwd=ROOT, capture_output=True)
                self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
                results.append((read_json(completed.stdout), tree_hashes(output)))
            self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
