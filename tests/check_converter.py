"""Интеграция настоящего парсера на синтетическом GameData, не приёмка реальных сейвов."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import struct
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.config import Config
from tools.extraction import converter_hashes, extract
from tools.io_utils import canonical, read_json, tree_hashes


def integer(value: int) -> bytes:
    return struct.pack("<i", value)


def string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return integer(len(raw)) + raw


def record(kind: int, sid: str, ints: dict[str, int], strings: dict[str, str]) -> bytes:
    raw = integer(0) + integer(kind) + integer(1) + string(sid) + string(sid) + integer(0)
    for category in ("bool", "float", "int", "vec3", "vec4", "string", "file"):
        values: Any = ints if category == "int" else strings if category == "string" else {}
        raw += integer(len(values))
        for key, value in values.items():
            raw += string(key) + (integer(value) if category == "int" else string(value))
    return raw + integer(0) + integer(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    converter_before = converter_hashes(args.converter)
    runs = []
    with tempfile.TemporaryDirectory(prefix="kenshi-parser-") as temp:
        root = Path(temp)
        game = root / "game"
        (game / "data").mkdir(parents=True)
        (game / "locale/ru_RU").mkdir(parents=True)
        (game / "locale/ru_RU/gamedata.po").write_bytes(b"")
        for filename in ("gamedata.base", "Newwworld.mod", "Dialogue.mod", "rebirth.mod"):
            (game / "data" / filename).write_bytes(struct.pack("<iiii", 17, 0, 0, 0))
        config = Config(args.converter, (root / "saves",), game, root / "workshop", "204-gamedata.base")
        for day in (1, 2):
            save = root / "saves" / str(day)
            (save / "platoon").mkdir(parents=True)
            state = record(56, "world", {"time day": day, "time hour": 3, "time minute": 15,
                                          "player money": 1234, "members": 0, "squads": 1}, {})
            squad = record(34, "a", {"squad index": 0}, {"platoon stringID": "a", "faction stringID": config.faction_sid})
            (save / "quick.save").write_bytes(struct.pack("<iii", 0, 2, 2) + state + squad)
            (save / "platoon/a.platoon").write_bytes(struct.pack("<iii", 0, 0, 0))
            before = tree_hashes(save)
            first = extract(save, "save", config, root / "snapshots", "synthetic", "main")
            snapshot = root / "snapshots" / first["snapshot_id"]
            hashes = tree_hashes(snapshot)
            second = extract(save, "save", config, root / "snapshots", "synthetic", "main")
            assert first == second and hashes == tree_hashes(snapshot)
            assert before == tree_hashes(save)
            current = read_json((snapshot / "current.json").read_bytes())
            assert current["world"] == {"day": day, "time": "03:15", "money": 1234}
            assert current["characters"] == [] and len(current["squads"]) == 1
            provenance = read_json((snapshot / "provenance.json").read_bytes())
            runs.append({"fixture_day": day, **first, "files": hashes,
                         "source_before": before, "source_after": tree_hashes(save),
                         "converter": provenance["converter"]})
    assert converter_before == converter_hashes(args.converter)
    ended = datetime.now(timezone.utc).isoformat()
    report = {"synthetic_only": True, "converter_unchanged": True,
              "started": started, "ended": ended, "runs": runs}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical(report))
    sys.stdout.buffer.write(canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
