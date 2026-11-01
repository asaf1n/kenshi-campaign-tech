"""CLI этапов извлечения, сравнения, рендеринга и публикации: python -m tools.campaign."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from tools.config import Config
from tools.diff import diff, diff_snapshots
from tools.extraction import ROOT, extract
from tools.io_utils import canonical, read_json
from tools.normalization import scrub
from tools.pipeline import run_pipeline
from tools.publisher import publish
from tools.rendering import render_brief, render_squad


def _resolve_snapshot_dir(target: str, snapshots_dir: Path) -> Path:
    p = Path(target)
    if p.is_dir() and (p / "current.json").is_file():
        return p.resolve()
    candidate = snapshots_dir / target
    if candidate.is_dir() and (candidate / "current.json").is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"Снимок не найден по пути или идентификатору: {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Управление кампанией Kenshi: extract, diff, render, publish, pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    # 1. extract
    extract_parser = commands.add_parser("extract", help="Извлечение неизменяемого снимка")
    extract_parser.add_argument("--source-kind", choices=("save", "export"), required=True)
    extract_parser.add_argument("--source", type=Path, required=True)
    extract_parser.add_argument("--output", type=Path)
    extract_parser.add_argument("--faction-sid")
    extract_parser.add_argument("--campaign-id")
    extract_parser.add_argument("--branch-id")
    extract_parser.add_argument("--parent-snapshot-id")

    # 2. check-schemas
    commands.add_parser("check-schemas", help="Проверка контрактных схем и примеров")

    # 3. diff
    diff_parser = commands.add_parser("diff", help="Сравнение двух снимков (kenshi-session-diff/5)")
    diff_parser.add_argument("--from", dest="from_snap", help="Предыдущий снимок (ID или путь к каталогу)")
    diff_parser.add_argument("--to", dest="to_snap", required=True, help="Текущий снимок (ID или путь к каталогу)")
    diff_parser.add_argument("--snapshots-dir", type=Path, default=ROOT / "data/snapshots")
    diff_parser.add_argument("--output", type=Path, help="Файл для сохранения diff (по умолчанию data/diffs/<diff_id>.json)")
    diff_parser.add_argument("--explain", action="store_true", help="Включить описание отслеживаемых полей")

    # 4. render
    render_parser = commands.add_parser("render", help="Генерация Markdown (brief.md, squad.md)")
    render_parser.add_argument("--current", type=Path, required=True, help="Путь к current.json")
    render_parser.add_argument("--changes", type=Path, required=True, help="Путь к changes.json")
    render_parser.add_argument("--output-dir", type=Path, required=True, help="Каталог для записи brief.md и squad.md")
    render_parser.add_argument("--snapshot-id", default="snapshot")

    # 5. publish
    pub_parser = commands.add_parser("publish", help="Атомарная публикация release bundle")
    pub_parser.add_argument("--snapshot-id", required=True)
    pub_parser.add_argument("--snapshots-dir", type=Path, default=ROOT / "data/snapshots")
    pub_parser.add_argument("--diffs-dir", type=Path, default=ROOT / "data/diffs")
    pub_parser.add_argument("--published-dir", type=Path, default=ROOT / "published")
    pub_parser.add_argument("--parent-snapshot-id")
    pub_parser.add_argument("--branch-id")
    pub_parser.add_argument("--campaign-id")
    pub_parser.add_argument("--dry-run", action="store_true")

    # 6. pipeline
    pipe_parser = commands.add_parser("pipeline", help="Сквозной срез: extract -> diff -> validate -> publish")
    pipe_parser.add_argument("--source-kind", choices=("save", "export"), required=True)
    pipe_parser.add_argument("--source", type=Path, required=True)
    pipe_parser.add_argument("--output", type=Path, help="Каталог снимков (data/snapshots)")
    pipe_parser.add_argument("--diffs-dir", type=Path, help="Каталог diff (data/diffs)")
    pipe_parser.add_argument("--published-dir", type=Path, help="Каталог публикации (published)")
    pipe_parser.add_argument("--faction-sid")
    pipe_parser.add_argument("--campaign-id")
    pipe_parser.add_argument("--branch-id")
    pipe_parser.add_argument("--parent-snapshot-id")
    pipe_parser.add_argument("--dry-run", action="store_true")

    # 7. build-world
    build_world_parser = commands.add_parser("build-world", help="Сборка версионированного справочника мира")
    build_world_parser.add_argument("--output", type=Path, help="Каталог или файл для записи")
    build_world_parser.add_argument("--mods", nargs="*", help="Переопределение списка активных модов")

    # 8. check-world
    check_world_parser = commands.add_parser("check-world", help="Проверка валидности справочника мира")
    check_world_parser.add_argument("--world-ref", required=True, help="Путь к файлу или ID справочника мира")

    args = parser.parse_args(argv)
    try:
        result: Any = None
        if args.command == "check-schemas":
            from tools.validation import check_examples
            result = {"validated_examples": check_examples()}

        elif args.command == "extract":
            result = extract(
                source=args.source,
                source_kind=args.source_kind,
                config=Config.from_env(args.faction_sid),
                output=args.output,
                campaign_id=args.campaign_id,
                branch_id=args.branch_id,
                parent_snapshot_id=args.parent_snapshot_id,
            )

        elif args.command == "diff":
            snapshots_dir = (args.snapshots_dir or ROOT / "data/snapshots").resolve()
            cur_dir = _resolve_snapshot_dir(args.to_snap, snapshots_dir)
            prev_dir = _resolve_snapshot_dir(args.from_snap, snapshots_dir) if args.from_snap else None
            output_dir = args.output.parent if args.output else (ROOT / "data/diffs")
            result = diff_snapshots(
                prev_dir=prev_dir,
                cur_dir=cur_dir,
                output_dir=output_dir,
                explain=args.explain,
            )
            if args.output:
                args.output.write_bytes(canonical(result))

        elif args.command == "render":
            cur = read_json(args.current.read_bytes())
            ch = read_json(args.changes.read_bytes())
            args.output_dir.mkdir(parents=True, exist_ok=True)
            brief_text = render_brief(cur, ch, args.snapshot_id)
            squad_text = render_squad(cur, args.snapshot_id)
            (args.output_dir / "brief.md").write_text(brief_text, encoding="utf-8")
            (args.output_dir / "squad.md").write_text(squad_text, encoding="utf-8")
            result = {
                "rendered": [
                    str((args.output_dir / "brief.md").resolve()),
                    str((args.output_dir / "squad.md").resolve()),
                ]
            }

        elif args.command == "publish":
            result = publish(
                snapshot_id=args.snapshot_id,
                snapshots_dir=args.snapshots_dir,
                diffs_dir=args.diffs_dir,
                published_dir=args.published_dir,
                parent_snapshot_id=args.parent_snapshot_id,
                branch_id=args.branch_id,
                campaign_id=args.campaign_id,
                dry_run=args.dry_run,
            )

        elif args.command == "pipeline":
            result = run_pipeline(
                source=args.source,
                source_kind=args.source_kind,
                config=Config.from_env(args.faction_sid),
                output_snapshots=args.output,
                output_diffs=args.diffs_dir,
                published_dir=args.published_dir,
                campaign_id=args.campaign_id,
                branch_id=args.branch_id,
                parent_snapshot_id=args.parent_snapshot_id,
                dry_run=args.dry_run,
            )

        elif args.command == "build-world":
            from tools.world_reference import build_world_reference, save_world_reference
            cfg = Config.from_env()
            w_ref = build_world_reference(cfg, mods_override=args.mods)
            saved_path = save_world_reference(w_ref, output=args.output)
            result = {
                "world_ref_id": w_ref["world_ref_id"],
                "path": str(saved_path),
                "total_locations": w_ref["meta"]["total_locations"],
                "game_version": w_ref["game_version"],
            }

        elif args.command == "check-world":
            from tools.world_reference import load_world_reference
            w_ref = load_world_reference(args.world_ref)
            from tools.validation import validate_world_reference
            result = validate_world_reference(w_ref)

        sys.stdout.buffer.write(canonical(result))
        return 0

    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
