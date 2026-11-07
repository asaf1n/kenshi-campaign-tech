"""Последовательный прогон проверок с сохранением evidence и таймкодов."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "tests" / ".evidence"

# Реестр допустимых пропусков: тест зависит от внешнего окружения (игра, сохранения, снимки)
# либо помечен LIVE-SLOT. Любой другой пропуск считается дефектом и роняет evidence.
SKIP_LINE = re.compile(r"\.\.\. skipped (['\"])(.*?)\1")
ALLOWED_SKIP = re.compile(r"^LIVE-SLOT|не найден|отсутствует")

# Внешний конвертер упразднён: шаг export зависит от его выгрузки и пропускается, если файла нет.
EXTERNAL_EXPORT = Path("C:/Users/artem/kenshi-platoon-export/kenshi-session.json")


def run_command(
    name: str,
    exec_args: list[str],
    display_cmd: list[str],
    log_name: str,
    run_name: str,
) -> dict:
    log_path = EVIDENCE_DIR / log_name
    run_path = EVIDENCE_DIR / run_name

    started = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        exec_args,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Дочерний Python пишет в кодировке консоли (cp1251 на Windows): без UTF-8 причины пропусков нечитаемы.
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    ended = datetime.now(timezone.utc).isoformat()

    log_path.write_text(completed.stdout, encoding="utf-8")

    run_meta = {
        "command": display_cmd,
        "started": started,
        "ended": ended,
        "exit_code": completed.returncode,
        "log": f"tests/.evidence/{log_name}",
    }
    run_path.write_text(json.dumps(run_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if completed.returncode != 0:
        print(f"FAILED: {name} (exit code {completed.returncode})", file=sys.stderr)
        print(completed.stdout, file=sys.stderr)
        sys.exit(completed.returncode)

    print(f"OK: {name} ({started} -> {ended})")
    return run_meta


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    # Очистка старых артефактов для гарантии актуальности mtime
    for old_file in EVIDENCE_DIR.glob("*"):
        if old_file.is_file():
            old_file.unlink()

    commands = [
        (
            "seed",
            [sys.executable, "tests/check_seed.py"],
            ["python", "tests/check_seed.py"],
            "seed.log",
            "seed-run.json",
        ),
        (
            "schemas",
            [sys.executable, "-m", "tools.campaign", "check-schemas"],
            ["python", "-m", "tools.campaign", "check-schemas"],
            "schemas.log",
            "schemas-run.json",
        ),
        (
            "build-world",
            [sys.executable, "-m", "tools.campaign", "build-world"],
            ["python", "-m", "tools.campaign", "build-world"],
            "build-world.log",
            "build-world-run.json",
        ),
        (
            "world_reference",
            [sys.executable, "-m", "unittest", "tests/test_world_reference.py", "-v"],
            ["python", "-m", "unittest", "tests/test_world_reference.py", "-v"],
            "world_reference.log",
            "world_reference-run.json",
        ),
        (
            "locations",
            [sys.executable, "-m", "unittest", "tests/test_locations.py", "-v"],
            ["python", "-m", "unittest", "tests/test_locations.py", "-v"],
            "locations.log",
            "locations-run.json",
        ),
        (
            "branches",
            [sys.executable, "-m", "unittest", "tests/test_branches.py", "-v"],
            ["python", "-m", "unittest", "tests/test_branches.py", "-v"],
            "branches.log",
            "branches-run.json",
        ),
        (
            "timeline",
            [sys.executable, "-m", "unittest", "tests/test_timeline.py", "-v"],
            ["python", "-m", "unittest", "tests/test_timeline.py", "-v"],
            "timeline.log",
            "timeline-run.json",
        ),
        (
            "converter",
            [
                sys.executable,
                "-B",
                "tests/check_converter.py",
                "--report",
                "tests/.evidence/converter.json",
            ],
            [
                "python",
                "-B",
                "tests/check_converter.py",
                "--report",
                "tests/.evidence/converter.json",
            ],
            "converter.log",
            "converter-run.json",
        ),
        (
            "export",
            [
                sys.executable,
                "-B",
                "tests/check_live.py",
                "--source-kind",
                "export",
                "--source",
                "C:/Users/artem/kenshi-platoon-export/kenshi-session.json",
                "--output",
                "data/snapshots",
                "--published-dir",
                "tests/.evidence/published_test",
                "--report",
                "tests/.evidence/export.json",
            ],
            [
                "python",
                "-B",
                "tests/check_live.py",
                "--source-kind",
                "export",
                "--source",
                "C:/Users/artem/kenshi-platoon-export/kenshi-session.json",
                "--output",
                "data/snapshots",
                "--published-dir",
                "tests/.evidence/published_test",
                "--report",
                "tests/.evidence/export.json",
            ],
            "export.log",
            "export-run.json",
        ),
        (
            "e2e_real",
            [
                sys.executable,
                "-B",
                "tests/check_e2e_real.py",
                "--published-dir",
                "tests/.evidence/published_test",
                "--report",
                "tests/.evidence/e2e_real.json",
            ],
            [
                "python",
                "-B",
                "tests/check_e2e_real.py",
                "--published-dir",
                "tests/.evidence/published_test",
                "--report",
                "tests/.evidence/e2e_real.json",
            ],
            "e2e_real.log",
            "e2e_real-run.json",
        ),
        (
            "unit",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
            "unit.log",
            "unit-run.json",
        ),
        (
            "diff",
            ["git", "diff", "--check"],
            ["git", "diff", "--check"],
            "diff.log",
            "diff-run.json",
        ),
    ]

    local_save = Path(os.environ.get("LOCALAPPDATA", "")).resolve() / "kenshi/save/autosave0"
    if local_save.is_dir() and (local_save / "quick.save").is_file():
        commands.insert(
            4,
            (
                "save",
                [
                    sys.executable,
                    "-B",
                    "tests/check_live.py",
                    "--source-kind",
                    "save",
                    "--source",
                    str(local_save),
                    "--output",
                    "data/snapshots",
                    "--published-dir",
                    "tests/.evidence/published_test",
                    "--report",
                    "tests/.evidence/save.json",
                ],
                [
                    "python",
                    "-B",
                    "tests/check_live.py",
                    "--source-kind",
                    "save",
                    "--source",
                    "%LOCALAPPDATA%/kenshi/save/autosave0",
                    "--output",
                    "data/snapshots",
                    "--published-dir",
                    "tests/.evidence/published_test",
                    "--report",
                    "tests/.evidence/save.json",
                ],
                "save.log",
                "save-run.json",
            ),
        )

    if not EXTERNAL_EXPORT.is_file():
        print(f"SKIP: внешний экспорт не найден ({EXTERNAL_EXPORT}): шаг export")
        commands = [command for command in commands if command[0] != "export"]

    for name, exec_args, display_cmd, log_name, run_name in commands:
        run_command(name, exec_args, display_cmd, log_name, run_name)

    unit_log = (EVIDENCE_DIR / "unit.log").read_text(encoding="utf-8")
    skipped = [reason for _, reason in SKIP_LINE.findall(unit_log)]
    unexpected = [reason for reason in skipped if not ALLOWED_SKIP.search(reason)]
    print(f"unit: {unit_log.count(' ... ok')} ok, {len(skipped)} skipped")
    if unexpected:
        print(f"FAILED: unit: пропуски вне реестра допустимых: {unexpected}", file=sys.stderr)
        return 1

    published = EVIDENCE_DIR / "published_test"
    sys.path.insert(0, str(ROOT))
    from tools.validation import PublicationValidationError, validate_publication

    if not (published / "tables" / "index.md").is_file():
        print(f"FAILED: в доказательном выпуске нет tables/index.md: {published}", file=sys.stderr)
        return 1
    try:
        validate_publication(published)
    except PublicationValidationError as error:
        print(f"FAILED: доказательный выпуск с tables/ не прошёл валидацию: {error}", file=sys.stderr)
        return 1
    print(f"tables: {len(list((published / 'tables').glob('*.csv')))} CSV в доказательном выпуске")

    print("All evidence collected successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
