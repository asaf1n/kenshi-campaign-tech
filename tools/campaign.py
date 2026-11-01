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
from tools.locator import resolve_locations
from tools.normalization import scrub
from tools.pipeline import run_pipeline
from tools.publisher import publish
from tools.rendering import render_brief, render_squad
from tools.world_reference import load_world_reference


def _resolve_snapshot_dir(target: str, snapshots_dir: Path) -> Path:
    p = Path(target)
    if p.is_dir() and (p / "current.json").is_file():
        return p.resolve()
    candidate = snapshots_dir / target
    if candidate.is_dir() and (candidate / "current.json").is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"Снимок не найден по пути или идентификатору: {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Управление кампанией Kenshi: extract, diff, locate, render, publish, pipeline")
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

    # 4. locate
    locate_parser = commands.add_parser("locate", help="Пространственное разрешение персонажей и построек (kenshi-locations/1)")
    locate_parser.add_argument("--current", type=Path, required=True, help="Путь к current.json")
    locate_parser.add_argument("--world-ref", required=True, help="Путь к файлу или ID справочника мира")
    locate_parser.add_argument("--snapshot-id", help="Идентификатор снимка")
    locate_parser.add_argument("--previous-locations", type=Path, help="Предыдущий locations.json для movement")
    locate_parser.add_argument("--changes", type=Path, help="changes.json для movement")
    locate_parser.add_argument("--output", type=Path, help="Файл для сохранения результата")

    # 5. render
    render_parser = commands.add_parser("render", help="Генерация Markdown (brief.md, squad.md)")
    render_parser.add_argument("--current", type=Path, required=True, help="Путь к current.json")
    render_parser.add_argument("--changes", type=Path, required=True, help="Путь к changes.json")
    render_parser.add_argument("--locations", type=Path, help="Путь к locations.json")
    render_parser.add_argument("--output-dir", type=Path, required=True, help="Каталог для записи brief.md и squad.md")
    render_parser.add_argument("--snapshot-id", default="snapshot")
    render_parser.add_argument("--timeline", type=Path, help="Опубликованная проекция timeline.jsonl")

    # 6. publish
    pub_parser = commands.add_parser("publish", help="Атомарная публикация release bundle")
    pub_parser.add_argument("--snapshot-id", required=True)
    pub_parser.add_argument("--snapshots-dir", type=Path, default=ROOT / "data/snapshots")
    pub_parser.add_argument("--diffs-dir", type=Path, default=ROOT / "data/diffs")
    pub_parser.add_argument("--published-dir", type=Path, default=ROOT / "published")
    pub_parser.add_argument("--parent-snapshot-id")
    pub_parser.add_argument("--branch-id")
    pub_parser.add_argument("--campaign-id")
    pub_parser.add_argument("--world-ref", help="Путь к файлу или ID справочника мира")
    pub_parser.add_argument("--index-file", type=Path, help="Путь к data/index.json")
    pub_parser.add_argument("--dry-run", action="store_true")

    # 7. pipeline
    pipe_parser = commands.add_parser("pipeline", help="Сквозной срез: extract -> diff -> resolve_locations -> render -> validate -> publish")
    pipe_parser.add_argument("--source-kind", choices=("save", "export"), required=True)
    pipe_parser.add_argument("--source", type=Path, required=True)
    pipe_parser.add_argument("--output", type=Path, help="Каталог снимков (data/snapshots)")
    pipe_parser.add_argument("--diffs-dir", type=Path, help="Каталог diff (data/diffs)")
    pipe_parser.add_argument("--published-dir", type=Path, help="Каталог публикации (published)")
    pipe_parser.add_argument("--faction-sid")
    pipe_parser.add_argument("--campaign-id")
    pipe_parser.add_argument("--branch-id")
    pipe_parser.add_argument("--parent-snapshot-id")
    pipe_parser.add_argument("--world-ref", help="Путь к файлу или ID справочника мира")
    pipe_parser.add_argument("--index-file", type=Path, help="Путь к data/index.json")
    pipe_parser.add_argument("--dry-run", action="store_true")
    for publication_parser in (pub_parser, pipe_parser):
        publication_parser.add_argument("--timeline", type=Path, help="Исходный timeline/events.jsonl")
        publication_parser.add_argument("--sessions-root", type=Path, help="Корень кампании с sessions/")

    # 8. build-world
    build_world_parser = commands.add_parser("build-world", help="Сборка версионированного справочника мира")
    build_world_parser.add_argument("--output", type=Path, help="Каталог или файл для записи")
    build_world_parser.add_argument("--mods", nargs="*", help="Переопределение списка активных модов")

    # 9. check-world
    check_world_parser = commands.add_parser("check-world", help="Проверка валидности справочника мира")
    check_world_parser.add_argument("--world-ref", required=True, help="Путь к файлу или ID справочника мира")

    # 10. index
    index_parser = commands.add_parser("index", help="Управление реестром снимков (data/index.json)")
    index_subs = index_parser.add_subparsers(dest="subcommand", required=True)

    index_show = index_subs.add_parser("show", help="Отображение содержимого реестра")
    index_show.add_argument("--index-file", type=Path, help="Путь к файлу индекса (data/index.json)")

    index_rebuild = index_subs.add_parser("rebuild", help="Восстановление реестра из provenance снимков")
    index_rebuild.add_argument("--snapshots-dir", type=Path, default=ROOT / "data/snapshots", help="Каталог снимков")
    index_rebuild.add_argument("--index-file", "--output", dest="index_file", type=Path, help="Файл индекса (data/index.json)")

    index_validate = index_subs.add_parser("validate", help="Проверка схемы и связей реестра")
    index_validate.add_argument("--index-file", type=Path, help="Путь к файлу индекса")

    # 11. branch
    branch_parser = commands.add_parser("branch", help="Управление ветками кампании")
    branch_subs = branch_parser.add_subparsers(dest="subcommand", required=True)

    branch_list = branch_subs.add_parser("list", help="Список доступных веток")
    branch_list.add_argument("--index-file", type=Path, help="Путь к файлу индекса")

    branch_switch = branch_subs.add_parser("switch", help="Переключение активной ветки")
    branch_switch.add_argument("branch_id", help="Идентификатор ветки")
    branch_switch.add_argument("--create", action="store_true", help="Создать ветку, если отсутствует")
    branch_switch.add_argument("--name", help="Отображаемое имя ветки")
    branch_switch.add_argument("--index-file", type=Path, help="Путь к файлу индекса")

    branch_tree = branch_subs.add_parser("tree", help="Дерево снимков и веток")
    branch_tree.add_argument("--index-file", type=Path, help="Путь к файлу индекса")

    timeline_parser = commands.add_parser("timeline", aliases=["event"], help="Журнал игровых событий")
    timeline_subs = timeline_parser.add_subparsers(dest="subcommand", required=True)
    for action in ("append", "import", "list", "validate", "session"):
        sub = timeline_subs.add_parser(action)
        sub.add_argument("--root", type=Path, default=ROOT, help="Корень кампании с data/, timeline/, sessions/")
        if action in ("append", "import"):
            sub.add_argument("source", type=Path, help="JSON события или JSONL пакет")
        elif action == "list":
            sub.add_argument("--branch-id")
            sub.add_argument("--resolved", action="store_true")
        elif action == "session":
            sub.add_argument("--session-id", required=True)
            sub.add_argument("--snapshot-id", action="append", required=True)
            sub.add_argument("--notes", type=Path, help="Авторские заметки; по умолчанию шаблон")

    args = parser.parse_args(argv)
    try:
        result: Any = None
        if args.command == "check-schemas":
            from tools.validation import check_examples
            result = {"validated_examples": check_examples()}

        elif args.command in {"timeline", "event"}:
            from tools.index import load_index
            from tools.timeline import append_event, create_session, filter_events_by_branch, import_events, load_sessions, read_event_batch, read_timeline, resolve_timeline
            from tools.validation import validate_timeline
            root = args.root.resolve()
            idx = load_index(root / "data/index.json")
            ledger = root / "timeline/events.jsonl"
            if args.subcommand == "session":
                result = create_session(args.session_id, args.snapshot_id, idx, root,
                                        args.notes.read_text(encoding="utf-8") if args.notes else None)
            elif args.subcommand == "append":
                result = append_event(read_json(args.source.read_bytes()), ledger, index_data=idx, root=root)
            elif args.subcommand == "import":
                result = import_events(read_event_batch(args.source), ledger, index_data=idx, root=root)
            else:
                events = read_timeline(ledger)
                result = validate_timeline(events, idx)
                if args.subcommand == "list":
                    if args.branch_id:
                        events = filter_events_by_branch(events, args.branch_id, idx, load_sessions(root, idx))
                    result = resolve_timeline(events) if args.resolved else events

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

        elif args.command == "locate":
            cur = read_json(args.current.read_bytes())
            w_ref = load_world_reference(args.world_ref)
            prev_loc = read_json(args.previous_locations.read_bytes()) if args.previous_locations else None
            ch = read_json(args.changes.read_bytes()) if args.changes else None
            result = resolve_locations(
                current=cur,
                world_ref=w_ref,
                snapshot_id=args.snapshot_id,
                previous_locations=prev_loc,
                changes=ch,
            )
            if args.output:
                args.output.write_bytes(canonical(result))

        elif args.command == "render":
            cur = read_json(args.current.read_bytes())
            ch = read_json(args.changes.read_bytes())
            loc = read_json(args.locations.read_bytes()) if args.locations else None
            args.output_dir.mkdir(parents=True, exist_ok=True)
            from tools.timeline import read_timeline
            timeline = read_timeline(args.timeline, projection=True) if args.timeline else None
            brief_text = render_brief(cur, ch, args.snapshot_id, locations=loc,
                                     timeline=timeline, timeline_projection=True)
            if timeline is not None:
                (args.output_dir / "timeline.jsonl").write_bytes(b"".join(canonical(e) for e in timeline))
            squad_text = render_squad(cur, args.snapshot_id, locations=loc)
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
                world_ref_id=args.world_ref,
                index_path=args.index_file,
                dry_run=args.dry_run,
                timeline_path=args.timeline,
                sessions_root=args.sessions_root,
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
                world_ref_id=args.world_ref,
                index_path=args.index_file,
                dry_run=args.dry_run,
                timeline_path=args.timeline,
                sessions_root=args.sessions_root,
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
            w_ref = load_world_reference(args.world_ref)
            from tools.validation import validate_world_reference
            result = validate_world_reference(w_ref)

        elif args.command == "index":
            from tools.index import load_index, rebuild_index
            from tools.validation import validate_index
            idx_file = getattr(args, "index_file", None) or (ROOT / "data/index.json")

            if args.subcommand == "show":
                result = load_index(idx_file)
            elif args.subcommand == "rebuild":
                result = rebuild_index(snapshots_dir=args.snapshots_dir, index_path=idx_file)
            elif args.subcommand == "validate":
                data = load_index(idx_file)
                result = validate_index(data)

        elif args.command == "branch":
            from tools.index import get_branch_tree, load_index, switch_branch
            idx_file = args.index_file or (ROOT / "data/index.json")

            if args.subcommand == "list":
                data = load_index(idx_file)
                result = {
                    "active_branch_id": data.get("active_branch_id"),
                    "branches": data.get("branches", {}),
                }
            elif args.subcommand == "switch":
                result = switch_branch(
                    branch_id=args.branch_id,
                    create=args.create,
                    name=args.name,
                    index_path=idx_file,
                )
            elif args.subcommand == "tree":
                data = load_index(idx_file)
                result = get_branch_tree(data)

        sys.stdout.buffer.write(canonical(result))
        return 0

    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
