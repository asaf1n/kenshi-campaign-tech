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
    diff_parser = commands.add_parser("diff", aliases=["period-diff"], help="Сравнение двух снимков или периода (kenshi-session-diff/5)")
    diff_parser.add_argument("--from", dest="from_snap", help="Предыдущий снимок (ID или путь к каталогу)")
    diff_parser.add_argument("--to", dest="to_snap", help="Текущий снимок (ID или путь к каталогу)")
    diff_parser.add_argument("--from-day", type=int, help="Начальный игровой день периода")
    diff_parser.add_argument("--to-day", type=int, help="Конечный игровой день периода")
    diff_parser.add_argument("--branch-id", help="Ветка для разрешения снимков по дням")
    diff_parser.add_argument("--index-file", type=Path, help="Путь к index.json")
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
    pub_parser.add_argument("--snapshot-id", nargs="?", default=None, help="Идентификатор снимка")
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
    pipe_parser.add_argument("--deliver-to", type=Path, help="Каталог игрового репозитория Kenshi-Compain для односторонней доставки")
    pipe_parser.add_argument("--deliver-push", action="store_true", help="Выполнить git push в игровом репозитории Kenshi-Compain")
    pipe_parser.add_argument("--allow-new-campaign", action="store_true", help="Пропустить проверку принадлежности сейва кампании (новый старт, смена ветки)")
    pipe_parser.add_argument("--sync-drive", action="store_true", help="Синхронизировать выгрузку и канон с Google Диском")
    for publication_parser in (pub_parser, pipe_parser):
        publication_parser.add_argument("--timeline", type=Path, help="Исходный timeline/events.jsonl")
        publication_parser.add_argument("--sessions-root", type=Path, help="Корень кампании с sessions/")
        publication_parser.add_argument("--screens-dir", type=Path, help="Каталог скриншотов игры (по умолчанию KENSHI_SCREENS_DIR или <KENSHI_DIR>/_screens)")

    # 7.1. annotate-screenshots
    ann_parser = commands.add_parser("annotate-screenshots", help="Разметка скриншотов и сборка ленты feed.md")
    ann_parser.add_argument("--screens-dir", type=Path, help="Каталог скриншотов игры")
    ann_parser.add_argument("--published-dir", type=Path, default=Path("published"), help="Каталог публикации (published)")

    # 7.1.1. индекс интерфейса кадров
    hud_parser = commands.add_parser("screenshots-index", help="Машинный индекс кадров по тексту интерфейса (Windows OCR)")
    hud_parser.add_argument("--screens-dir", type=Path, help="Каталог скриншотов игры")
    hud_parser.add_argument("--all", action="store_true", help="Весь каталог вместо окна 7 суток")
    find_parser = commands.add_parser("screenshots-find", help="Поиск кадров в машинном индексе")
    find_parser.add_argument("--day", type=int, help="Игровой день, прочитанный с кадра")
    find_parser.add_argument("--text", help="Подстрока распознанного текста")

    # 7.2. deliver-companion
    deliv_parser = commands.add_parser("deliver-companion", help="Доставка проверенного published в игровой репозиторий Kenshi-Compain")
    deliv_parser.add_argument("--published-dir", type=Path, help="Каталог публикации (по умолчанию published)")
    deliv_parser.add_argument("--companion-dir", type=Path, help="Каталог репозитория Kenshi-Compain")
    deliv_parser.add_argument("--no-commit", action="store_true", help="Не выполнять git commit в Kenshi-Compain")
    deliv_parser.add_argument("--push", action="store_true", help="Выполнить git push в Kenshi-Compain")

    # 7.3. sync-drive
    drive_parser = commands.add_parser("sync-drive", help="Синхронизация выгрузки и канона с Google Диском")
    drive_parser.add_argument("--scope", choices=("all", "session", "chronicle", "docs"), default="all",
                              help="all = session + docs; Campaign/ пишет только chronicle")
    drive_parser.add_argument("--published-dir", type=Path, default=ROOT / "published")
    drive_parser.add_argument("--companion-dir", type=Path, help="Каталог репозитория Kenshi-Compain")
    drive_parser.add_argument("--gdrive-dir", type=Path, help="Корень проекта на Google Диске (по умолчанию KENSHI_GDRIVE_DIR или автопоиск)")
    drive_parser.add_argument("--dry-run", action="store_true", help="Только план: ничего не писать и не удалять на Диске")
    drive_parser.add_argument("--prune", action="store_true", help="Удалить с Диска выпавшие из набора файлы, известные репозиториям (в корзину); без флага только перечисляются")

    # 7.4. ingest-inbox
    inbox_parser = commands.add_parser("ingest-inbox", help="Приём пакетов из tmp/inbox/ Google Диска: запись в Campaign/, commit и push в Kenshi-Compain")
    inbox_parser.add_argument("--companion-dir", type=Path, help="Каталог репозитория Kenshi-Compain")
    inbox_parser.add_argument("--gdrive-dir", type=Path, help="Корень проекта на Google Диске")

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

    record_parser = timeline_subs.add_parser("record", help="Добавить подтвержденное игровое событие")
    record_parser.add_argument("--root", type=Path, default=ROOT, help="Корень кампании")
    record_parser.add_argument("--event-id", required=True, help="ID события")
    record_parser.add_argument("--session-id", required=True, help="ID сессии")
    record_parser.add_argument("--summary", required=True, help="Описание события")
    record_parser.add_argument("--day", type=int, help="Игровой день")
    record_parser.add_argument("--snapshot-id", help="Снимок")
    record_parser.add_argument("--kind", default="user_action", help="Категория события")
    record_parser.add_argument("--status", choices=["confirmed", "observed", "inferred"], default="confirmed")
    record_parser.add_argument("--evidence-kind", choices=["screenshot", "user_note", "snapshot", "diff", "other"], default="user_note")
    record_parser.add_argument("--evidence-ref", default="user_note")
    record_parser.add_argument("--evidence-note")

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
            elif args.subcommand == "record":
                from datetime import datetime, timezone
                ev_item: dict[str, Any] = {"kind": args.evidence_kind, "ref": args.evidence_ref}
                if args.evidence_note:
                    ev_item["note"] = args.evidence_note
                event_data = {
                    "format": "kenshi-event/1",
                    "event_id": args.event_id,
                    "session_id": args.session_id,
                    "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "game_day": args.day,
                    "snapshot_id": args.snapshot_id,
                    "kind": args.kind,
                    "status": args.status,
                    "summary": args.summary,
                    "evidence": [ev_item],
                }
                result = append_event(event_data, ledger, index_data=idx, root=root)
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

        elif args.command in {"diff", "period-diff"}:
            from tools.index import find_snapshot_by_day, load_index
            snapshots_dir = (args.snapshots_dir or ROOT / "data/snapshots").resolve()
            idx_file = (args.index_file or (snapshots_dir.parent / "index.json")).resolve()
            idx_data = None
            if idx_file.is_file():
                try:
                    idx_data = load_index(idx_file)
                except Exception:
                    idx_data = None

            from_snap = args.from_snap
            to_snap = args.to_snap

            if args.from_day is not None and idx_data:
                resolved_from = find_snapshot_by_day(idx_data, args.from_day, args.branch_id)
                if resolved_from:
                    from_snap = resolved_from

            if args.to_day is not None and idx_data:
                resolved_to = find_snapshot_by_day(idx_data, args.to_day, args.branch_id)
                if resolved_to:
                    to_snap = resolved_to

            if not to_snap:
                raise ValueError("Не указан целевой снимок (--to или --to-day)")

            cur_dir = _resolve_snapshot_dir(to_snap, snapshots_dir)
            prev_dir = _resolve_snapshot_dir(from_snap, snapshots_dir) if from_snap else None
            output_dir = args.output.parent if args.output else (ROOT / "data/diffs")
            result = diff_snapshots(
                prev_dir=prev_dir,
                cur_dir=cur_dir,
                output_dir=output_dir,
                explain=args.explain,
                index_data=idx_data,
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
            effective_screens_dir = args.screens_dir or Config.from_env().screens
            snap_id = args.snapshot_id
            if not snap_id:
                manifest_file = args.published_dir / "manifest.json"
                if manifest_file.is_file():
                    snap_id = read_json(manifest_file.read_bytes()).get("snapshot_id")
            if not snap_id:
                raise ValueError("snapshot_id обязателен для публикации при отсутствии существующего manifest.json")
            result = publish(
                snapshot_id=snap_id,
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
                screens_dir=effective_screens_dir,
            )

        elif args.command == "annotate-screenshots":
            from datetime import datetime, timezone, timedelta
            from tools.screenshots import collect_recent_screenshots
            from tools.screenshot_clustering import cluster_screenshots
            from tools.screenshot_extractor import extract_screenshots_batch
            from tools.screenshot_feed import render_screenshot_feed, serialize_annotations_jsonl
            from tools.screenshot_hud import update_hud_index

            screens_dir = args.screens_dir or Config.from_env().screens
            pub_dir = args.published_dir.resolve()
            now_dt = datetime.now(timezone.utc)
            cutoff_dt = now_dt - timedelta(days=7)
            collected = collect_recent_screenshots(screens_dir, cutoff_dt, now_dt)
            clustered, episodes = cluster_screenshots(collected)
            prev_ann_p = pub_dir / "screenshots/annotations.jsonl"
            now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            hud_rows, hud_warning = update_hud_index(collected, now_iso=now_iso)
            annotated = extract_screenshots_batch(
                clustered, prev_ann_p if prev_ann_p.is_file() else None, now_iso=now_iso, hud_rows=hud_rows,
            )
            sc_dir = pub_dir / "screenshots"
            sc_dir.mkdir(parents=True, exist_ok=True)
            (sc_dir / "annotations.jsonl").write_bytes(serialize_annotations_jsonl(annotated))
            (sc_dir / "feed.md").write_text(
                render_screenshot_feed(
                    annotated, episodes, generated_at=now_iso, window_days=7,
                    window_start=cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), window_end=now_iso
                ),
                encoding="utf-8"
            )
            result = {"annotated_count": len(annotated), "episodes_count": len(episodes)}
            if hud_warning:
                result["warning"] = hud_warning

        elif args.command == "screenshots-index":
            from datetime import datetime, timezone, timedelta
            from tools.screenshots import collect_recent_screenshots
            from tools.screenshot_hud import update_hud_index

            now_dt = datetime.now(timezone.utc)
            cutoff_dt = datetime.fromtimestamp(0, timezone.utc) if args.all else now_dt - timedelta(days=7)
            collected = collect_recent_screenshots(args.screens_dir or Config.from_env().screens, cutoff_dt, now_dt)
            rows, hud_warning = update_hud_index(collected)
            frames = [rows[item["sha256"]] for item in collected if item["sha256"] in rows]
            result = {
                "frames": len(collected),
                "indexed": len(frames),
                "with_day": sum(row["game_day"] is not None for row in frames),
                "with_time": sum(row["game_time"] is not None for row in frames),
                "with_selected": sum(row["selected"] is not None for row in frames),
            }
            if hud_warning:
                result["warning"] = hud_warning

        elif args.command == "screenshots-find":
            from tools.screenshot_hud import find_frames, load_hud_index

            fields = ("filename", "modified_at", "game_day", "game_time", "selected", "state", "goal", "tasks")
            result = {"frames": [
                {field: row.get(field) for field in fields}
                for row in find_frames(load_hud_index().values(), args.day, args.text)
            ]}

        elif args.command == "pipeline":
            cfg = Config.from_env(args.faction_sid)
            result = run_pipeline(
                source=args.source,
                source_kind=args.source_kind,
                config=cfg,
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
                screens_dir=args.screens_dir or cfg.screens,
                allow_new_campaign=args.allow_new_campaign,
            )
            if getattr(args, "deliver_to", None) and result.get("publish", {}).get("manifest", {}).get("validation", {}).get("status") == "passed":
                from tools.deliver_companion import deliver_to_companion
                pub_p = args.published_dir or Path("published")
                deliv_res = deliver_to_companion(
                    published_dir=pub_p,
                    companion_dir=args.deliver_to,
                    auto_commit=True,
                    auto_push=getattr(args, "deliver_push", False),
                )
                result["companion_delivery"] = deliv_res
            if getattr(args, "sync_drive", False) and not args.dry_run:
                from tools.deliver_companion import companion_root
                from tools.gdrive_sync import PIPELINE_SCOPE, sync_drive
                try:
                    result["gdrive"] = sync_drive(cfg.gdrive_root, (args.published_dir or ROOT / "published").resolve(),
                                                  companion_root(args.deliver_to), scope=PIPELINE_SCOPE)
                except Exception as sync_error:
                    # Основная публикация уже завершена: сбой Диска её не отменяет.
                    sys.stderr.buffer.write(f"WARNING: синхронизация Google Диска: {sync_error}\n".encode("utf-8"))
                    result["gdrive"] = {"status": "failed"}

        elif args.command == "deliver-companion":
            from tools.deliver_companion import deliver_to_companion
            result = deliver_to_companion(
                published_dir=args.published_dir,
                companion_dir=args.companion_dir,
                auto_commit=not args.no_commit,
                auto_push=args.push,
            )

        elif args.command == "sync-drive":
            from tools.config import find_gdrive_root
            from tools.deliver_companion import companion_root
            from tools.gdrive_sync import sync_drive
            result = sync_drive(args.gdrive_dir or find_gdrive_root(), args.published_dir.resolve(),
                                companion_root(args.companion_dir), scope=args.scope,
                                dry_run=args.dry_run, prune=args.prune)

        elif args.command == "ingest-inbox":
            from tools.config import find_gdrive_root
            from tools.deliver_companion import companion_root
            from tools.gdrive_sync import ingest_inbox
            result = ingest_inbox(args.gdrive_dir or find_gdrive_root(), companion_root(args.companion_dir))

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
        if args.command == "sync-drive" and result.get("status") != "synced":
            return 1
        if args.command == "screenshots-index" and result["indexed"] < result["frames"]:
            return 1
        delivery = result.get("companion_delivery") if args.command == "pipeline" else result
        if args.command in ("pipeline", "deliver-companion") and delivery and delivery.get("status") == "partial":
            return 1
        if args.command == "ingest-inbox" and (
                result.get("rejected") or result.get("error") or any("move_error" in item for item in result["processed"])):
            return 1
        return 0

    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
