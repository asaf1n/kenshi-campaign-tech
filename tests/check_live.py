"""Проверка реального экспорта/сейва: extract -> diff -> validate -> publish."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.extraction import ROOT
from tools.io_utils import canonical, file_hash, read_json, tree_hashes
from tools.normalization import normalize
from tools.publisher import publish
from tools.validation import validate_publication


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-kind", choices=("export", "save"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--published-dir",
        type=Path,
        default=None,
        help="каталог выпуска; по умолчанию временный (реальный published/ только явно)",
    )
    args = parser.parse_args()
    if args.published_dir is None:
        args.published_dir = Path(tempfile.mkdtemp(prefix="check_live_published_"))
    if args.output.resolve().is_relative_to((ROOT / "data").resolve()):
        parser.error("--output внутри рабочих data/: укажите отдельный каталог проверки, например tests/.evidence/check_live/snapshots")

    overall_started = datetime.now(timezone.utc).isoformat()
    before = tree_hashes(args.source) if args.source_kind == "save" else file_hash(args.source)
    runs = []
    contents = []

    # 1. Проверка повторяемости извлечения при разных PYTHONHASHSEED
    for seed in ("1", "42"):
        command = [
            sys.executable, "-B", "-m", "tools.campaign", "extract",
            "--source-kind", args.source_kind,
            "--source", str(args.source),
            "--output", str(args.output),
        ]

        started = datetime.now(timezone.utc).isoformat()
        completed = subprocess.run(command, cwd=ROOT, env=os.environ | {"PYTHONHASHSEED": seed}, capture_output=True)
        ended = datetime.now(timezone.utc).isoformat()
        if completed.returncode:
            sys.stderr.buffer.write(completed.stderr)
            return completed.returncode

        result = read_json(completed.stdout)
        snapshot = args.output / result["snapshot_id"]
        current_raw = (snapshot / "current.json").read_bytes()
        provenance_raw = (snapshot / "provenance.json").read_bytes()
        current = read_json(current_raw)

        for raw in (current_raw, provenance_raw):
            for forbidden in (b"Users/artem", b"Users\\\\artem", b"/home/"):
                assert forbidden not in raw
        assert "changes" not in current and "path" not in current["save"]
        assert "exported_at" not in current["meta"] and "timings_ms" not in current["meta"]

        if args.source_kind == "export":
            original = read_json(args.source.read_bytes())
            assert current == normalize(original)[0]
            assert current["world"] == original["world"]
            assert {c["id"]: c.get("skills_base") for c in current["characters"]} == {
                c["id"]: c.get("skills_base") for c in original["characters"]
            }

        contents.append((current_raw, provenance_raw))
        runs.append({
            "pythonhashseed": seed,
            "started": started,
            "ended": ended,
            "exit_code": completed.returncode,
            **result,
            "files": tree_hashes(snapshot),
            "characters": len(current["characters"]),
            "squads": len(current["squads"]),
            "world": current["world"],
        })

    after = tree_hashes(args.source) if args.source_kind == "save" else file_hash(args.source)
    assert before == after
    assert contents[0] == contents[1]

    # 2. Проверка сквозной публикации извлечённого снимка
    snapshot_id = runs[0]["snapshot_id"]
    # Рабочие data/ (индекс, дельты) не затрагиваются: публикация проверки пишет только во временный каталог.
    work_dir = Path(tempfile.mkdtemp(prefix="check_live_data_"))
    gitkeep_before = (args.published_dir / ".gitkeep").read_bytes() if (args.published_dir / ".gitkeep").is_file() else b""

    pub_result = publish(
        snapshot_id=snapshot_id,
        snapshots_dir=args.output,
        diffs_dir=work_dir / "diffs",
        published_dir=args.published_dir,
        campaign_id="validation",
        index_path=work_dir / "index.json",
    )
    assert pub_result["status"] == "published"

    # Валидация пакета публикации
    val_result = validate_publication(args.published_dir)
    assert val_result["status"] == "passed"

    # Проверка сохранения .gitkeep
    assert (args.published_dir / ".gitkeep").read_bytes() == gitkeep_before

    # Сверка чисел brief.md с JSON
    brief_text = (args.published_dir / "brief.md").read_text(encoding="utf-8")
    squad_text = (args.published_dir / "squad.md").read_text(encoding="utf-8")
    cur_pub = read_json((args.published_dir / "extended" / "current.json").read_bytes())
    if cur_pub.get("world", {}).get("day") is not None:
        assert f"День {cur_pub['world']['day']}" in brief_text
    assert f"**Всего персонажей:** {len(cur_pub['characters'])}" in brief_text

    overall_ended = datetime.now(timezone.utc).isoformat()
    report = {
        "source_kind": args.source_kind,
        "source_before": before,
        "source_after": after,
        "identical_bytes": True,
        "publication": pub_result,
        "validation": val_result,
        "started": overall_started,
        "ended": overall_ended,
        "runs": runs,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical(report))
    sys.stdout.buffer.write(canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
