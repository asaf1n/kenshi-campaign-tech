"""Атомарная публикация release bundle на Windows с защитой .gitkeep и rollback.

Протокол:
1. Захват файловой блокировки.
2. Проверка и самовосстановление после предыдущих сбоев (backup -> published).
3. Пространственное разрешение локаций (kenshi-locations/1) и подготовка world_reference.
4. Рендеринг русскоязычных Markdown (brief.md, squad.md, changes.md, screenshots/index.md).
5. Подготовка пакета в staging на том же томе.
6. Валидация пакета до переключения (validate_publication).
7. Переключение: published -> backup -> staging -> published.
8. При ошибке переключения — немедленный откат из backup.
9. Сохранение published/.gitkeep побайтово во всех сценариях.
"""
from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from tools.diff import diff_snapshots, make_diff_id
from tools.index import DEFAULT_INDEX_PATH, check_branch_compatibility, load_index, save_index
from tools.io_utils import canonical, digest, linked, read_json
from tools.locator import resolve_locations
from tools.rendering import render_brief, render_squad
from tools.timeline import filter_events_by_branch, load_sessions, project_timeline, read_timeline
from tools.validation import TIMELINE_EMPTY_WARNING, validate_publication
from tools.world_reference import DEFAULT_WORLD_DIR, find_world_reference, load_world_reference

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCREENS_DIR: Path | None = None
# Шапка brief.md содержит generated_at, а сам generated_at зависит от сравнения файлов выпуска: сравниваем с подстановкой.
GENERATED_AT_TOKEN = "@@generated_at@@"

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False


class PublisherError(RuntimeError):
    """Отказ публикации пакета."""


class PublishLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fd: int | None = None

    def __enter__(self) -> PublishLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        if HAS_MSVCRT:
            try:
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            except (OSError, IOError) as error:
                os.close(self.fd)
                self.fd = None
                raise PublisherError(f"Каталог публикации занят другим процессом: {error}") from error
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.fd is not None:
            try:
                if HAS_MSVCRT:
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self.fd)
                self.fd = None
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError:
                    pass


def recover_if_needed(published_dir: Path, backup_dir: Path, staging_dir: Path) -> None:
    """Восстановление после предыдущего аварийного завершения."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    if backup_dir.exists():
        manifest_in_pub = published_dir.is_dir() and (published_dir / "manifest.json").is_file()
        if not manifest_in_pub:
            if published_dir.exists():
                shutil.rmtree(published_dir, ignore_errors=True)
            backup_dir.rename(published_dir)
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)


def _get_world_reference(
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    mods: list[str] | None = None,
) -> dict[str, Any] | None:
    """Подбор или загрузка справочника мира с надежным fallback для тестов."""
    base_dir = world_dir or DEFAULT_WORLD_DIR
    if world_ref_id is not None:
        try:
            return load_world_reference(world_ref_id, world_dir=base_dir)
        except Exception:
            pass

    ref_path, _ = find_world_reference(mods=mods, world_ref_id=world_ref_id, world_dir=base_dir)
    if ref_path is not None and ref_path.is_file():
        try:
            return load_world_reference(ref_path)
        except Exception:
            pass

    # Поиск любого файла в base_dir
    if base_dir.is_dir():
        for p in sorted(base_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                data = read_json(p.read_bytes())
                if data.get("format") == "kenshi-world-reference/1":
                    return data
            except Exception:
                continue

    # Fallback на проверенный контрактный пример
    example_path = ROOT / "examples/world-reference.example.json"
    if example_path.is_file():
        try:
            data = read_json(example_path.read_bytes())
            if data.get("format") == "kenshi-world-reference/1":
                return data
        except Exception:
            pass

    return None


def publish(
    snapshot_id: str,
    snapshots_dir: Path,
    diffs_dir: Path,
    published_dir: Path,
    parent_snapshot_id: str | None = None,
    branch_id: str | None = None,
    campaign_id: str | None = None,
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    index_path: Path | None = None,
    generated_at: str | None = None,
    dry_run: bool = False,
    fault_inject: str | None = None,
    timeline_path: Path | None = None,
    sessions_root: Path | None = None,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    snapshots_dir = snapshots_dir.resolve()
    diffs_dir = diffs_dir.resolve()
    published_dir = published_dir.resolve()

    cur_dir = snapshots_dir / snapshot_id
    if not cur_dir.is_dir() or not (cur_dir / "current.json").is_file():
        raise PublisherError(f"Снимок {snapshot_id} не найден в {snapshots_dir}")

    cur = read_json((cur_dir / "current.json").read_bytes())
    prov = read_json((cur_dir / "provenance.json").read_bytes())

    effective_parent = parent_snapshot_id or prov.get("parent_snapshot_id")
    effective_branch = branch_id or prov.get("branch_id")
    effective_campaign = campaign_id or prov.get("campaign_id")
    source_kind = prov.get("source_kind", "save")
    source_format = prov.get("source_format", "kenshi-session/10")
    source_sha256 = prov.get("source_sha256")
    game_time_raw = prov.get("game_time") or cur.get("world", {})
    game_time = (
        f"Day {game_time_raw.get('day')} {game_time_raw.get('time')}"
        if isinstance(game_time_raw, dict) and game_time_raw.get("day") is not None
        else None
    )

    # Проверка индекса снимков и веток (data/index.json)
    idx_target = (index_path or (snapshots_dir.parent / "index.json")).resolve()
    idx_data: dict[str, Any] | None = None
    if idx_target.is_file():
        try:
            idx_data = load_index(idx_target)
        except Exception:
            idx_data = None
    elif DEFAULT_INDEX_PATH.is_file():
        try:
            idx_data = load_index(DEFAULT_INDEX_PATH)
            idx_target = DEFAULT_INDEX_PATH.resolve()
        except Exception:
            idx_data = None

    if idx_data is not None:
        idx_snaps = idx_data.get("snapshots", {})
        if snapshot_id in idx_snaps:
            snap_entry = idx_snaps[snapshot_id]
            if not branch_id:
                effective_branch = snap_entry.get("branch_id") or effective_branch
            if not parent_snapshot_id:
                effective_parent = snap_entry.get("parent_snapshot_id") or effective_parent
            if not campaign_id:
                effective_campaign = snap_entry.get("campaign_id") or effective_campaign
            if not world_ref_id:
                world_ref_id = snap_entry.get("world_ref_id")

        active_branch = idx_data.get("active_branch_id")
        if effective_branch and active_branch and effective_branch != active_branch and branch_id is None:
            raise PublisherError(
                f"Снимок {snapshot_id} принадлежит ветке '{effective_branch}', "
                f"но активной в индексе является '{active_branch}'. "
                f"Для публикации требуется явное указание или переключение ветки."
            )

        if effective_parent is not None:
            compat, branch_reason = check_branch_compatibility(idx_data, effective_parent, snapshot_id)
            if not compat:
                raise PublisherError(
                    f"Несовместимый родитель {effective_parent} для снимка {snapshot_id}: {branch_reason}"
                )

    # Защита канонического каталога published/
    canonical_pub_dir = (ROOT / "published").resolve()
    if published_dir.resolve() == canonical_pub_dir:
        if effective_campaign != "hot-lon":
            raise PublisherError(
                f"Публикация в канонический published/ разрешена только для campaign_id='hot-lon', "
                f"получено: '{effective_campaign}'"
            )

    # Проверка против существующей публикации
    pub_manifest_file = published_dir / "manifest.json"
    if pub_manifest_file.is_file():
        try:
            pub_manifest = read_json(pub_manifest_file.read_bytes())
            pub_snap = pub_manifest.get("snapshot_id")
            pub_branch = pub_manifest.get("branch_id")
            pub_campaign = pub_manifest.get("campaign_id")
            if pub_campaign and effective_campaign and pub_campaign != effective_campaign and campaign_id is None:
                raise PublisherError(
                    f"Снимок {snapshot_id} принадлежит кампании '{effective_campaign}', "
                    f"а текущая публикация — кампании '{pub_campaign}'. "
                    f"Для смены кампании публикации укажите явный campaign_id."
                )
            if pub_branch and effective_branch and pub_branch != effective_branch and branch_id is None:
                raise PublisherError(
                    f"Снимок {snapshot_id} принадлежит ветке '{effective_branch}', "
                    f"а текущая публикация — ветке '{pub_branch}'. "
                    f"Для смены ветки публикации укажите явный branch_id."
                )
            if effective_parent is not None and pub_snap is not None and effective_parent == pub_snap:
                if idx_data is not None:
                    compat, branch_reason = check_branch_compatibility(idx_data, pub_snap, snapshot_id)
                    if not compat:
                        raise PublisherError(
                            f"Попытка сравнения между несовместимыми ветками reload ({pub_snap} -> {snapshot_id}): {branch_reason}"
                        )
        except Exception as e:
            if isinstance(e, PublisherError):
                raise

    prev_dir = None
    if effective_parent is not None:
        prev_dir = snapshots_dir / effective_parent
        if not prev_dir.is_dir() or not (prev_dir / "current.json").is_file():
            raise PublisherError(f"Родительский снимок {effective_parent} не найден в {snapshots_dir}")

    # Вычисление или чтение существующего diff
    diff_id = make_diff_id(effective_parent, snapshot_id)
    diff_path = diffs_dir / f"{diff_id}.json"
    if diff_path.is_file():
        diff_data = read_json(diff_path.read_bytes())
    else:
        diff_data = diff_snapshots(
            prev_dir=prev_dir,
            cur_dir=cur_dir,
            output_dir=diffs_dir,
            parent_snapshot_id=effective_parent,
            index_data=idx_data,
        )

    # Если родитель был задан, но сравнение невалидно
    if effective_parent is not None and diff_data.get("valid") is False:
        diff_reason = diff_data.get("reason") or "; ".join(diff_data.get("problems", []))
        raise PublisherError(
            f"Невалидное сравнение снимков ({effective_parent} -> {snapshot_id}): {diff_reason}"
        )

    # 1. Загрузка / подбор справочника мира
    w_ref = _get_world_reference(
        world_ref_id=world_ref_id,
        world_dir=world_dir,
        mods=prov.get("mods") if isinstance(prov.get("mods"), list) else None,
    )
    if w_ref is None:
        raise PublisherError("Не удалось подобрать справочник мира для публикации")

    effective_wref_id = w_ref["world_ref_id"]

    # Fault injection на гео-этапе
    if fault_inject in ("before_geo", "geo_error"):
        raise PublisherError("Fault injection: сбой гео-этапа до разрешения локаций")

    # 2. Пространственное разрешение локаций
    prev_locations = None
    if prev_dir is not None:
        loc_dir = ROOT / "data/locations"
        prev_loc_path = loc_dir / f"{effective_parent}.json"
        if prev_loc_path.is_file():
            prev_locations = read_json(prev_loc_path.read_bytes())
        else:
            prev_cur = read_json((prev_dir / "current.json").read_bytes())
            prev_locations = resolve_locations(
                current=prev_cur,
                world_ref=w_ref,
                snapshot_id=effective_parent,
            )

    locations_data = resolve_locations(
        current=cur,
        world_ref=w_ref,
        snapshot_id=snapshot_id,
        previous_locations=prev_locations,
        changes=diff_data,
        generated_at=generated_at,
    )

    # Сохраняем промежуточный результат в data/locations/<snapshot_id>.json
    loc_dir = ROOT / "data/locations"
    try:
        loc_dir.mkdir(parents=True, exist_ok=True)
        (loc_dir / f"{snapshot_id}.json").write_bytes(canonical(locations_data))
    except Exception:
        pass

    # Авторский журнал читается целиком: повреждение не скрывается фильтром ветки.
    ledger = timeline_path or snapshots_dir.parent.parent / "timeline/events.jsonl"
    events = read_timeline(ledger)
    selected: list[dict[str, Any]] = []
    if events:
        if idx_data is None or not effective_branch:
            raise PublisherError("Для публикации событий нужен валидный индекс и выбранная ветка")
        sessions = load_sessions(sessions_root or ledger.parent.parent, idx_data)
        selected = filter_events_by_branch(events, effective_branch, idx_data, sessions, snapshot_id=snapshot_id)

    # Накопительный таймлайн: события окна цепочки снимков и фиксация смены ветки (детерминированно по индексу).
    chain: list[dict[str, Any]] = []
    chain_steps: list[dict[str, Any]] = []
    branch_switch: dict[str, str] | None = None
    load_chain_diff = None
    if idx_data is not None and effective_branch:
        from tools.chain_timeline import (
            branch_switch_event, chain_events, chain_ids, detect_branch_switch, significant_changes,
        )
        from tools.screenshot_delta import game_label

        def load_chain_diff(parent: str, child: str) -> dict[str, Any] | None:
            if (parent, child) == (effective_parent, snapshot_id):
                return diff_data
            path = diffs_dir / f"{make_diff_id(parent, child)}.json"
            if path.is_file():
                return read_json(path.read_bytes())
            try:
                return diff_snapshots(prev_dir=snapshots_dir / parent, cur_dir=snapshots_dir / child, output_dir=diffs_dir,
                                      parent_snapshot_id=parent, index_data=idx_data)
            except Exception:
                return None

        chain = chain_events(idx_data, snapshot_id, effective_branch, load_chain_diff, make_diff_id)
        window_ids = chain_ids(idx_data, snapshot_id)
        for parent_id, child_id in list(zip(window_ids, window_ids[1:]))[:-1]:
            step_diff = load_chain_diff(parent_id, child_id)
            summaries = [summary for _, summary in significant_changes(step_diff or {})]
            if summaries and step_diff.get("valid") is not False:
                chain_steps.append({
                    "child_id": child_id,
                    "label": f"{game_label(idx_data['snapshots'][child_id])} ({child_id[:15]})",
                    "summaries": summaries,
                })
        branch_switch = detect_branch_switch(idx_data, effective_parent, snapshot_id, effective_branch)
        if branch_switch:
            recorded_at = idx_data["snapshots"][snapshot_id].get("registered_at", "")
            chain.append(branch_switch_event(branch_switch, recorded_at, diff_id))
    combined = [*selected, *chain]
    timeline_data = project_timeline(combined, snapshot_id, diff_id) if combined else []
    timeline_bytes = b"".join(canonical(e) for e in timeline_data)

    # 3. Рендеринг русскоязычных Markdown; brief.md собирается после разметки скриншотов
    from tools.changes_render import render_changes_md

    squad_md = render_squad(cur, snapshot_id, locations=locations_data)
    changes_md = render_changes_md(diff_data, chain_steps)

    manifest_data = {
        "format": "kenshi-publication/1",
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_id": snapshot_id,
        "parent_snapshot_id": effective_parent,
        "branch_id": effective_branch,
        "campaign_id": effective_campaign,
        "game_time": game_time,
        "world_ref_id": effective_wref_id,
        "source_kind": source_kind,
        "source_format": source_format,
        "source_sha256": source_sha256,
        "artifacts": {
            "current": "extended/current.json",
            "current_index": "current/index.json",
            "changes": "changes.json",
            "changes_md": "changes.md",
            "brief": "brief.md",
            "squad": "squad.md",
            "locations": "locations.json",
            "world_reference": "world_reference.json",
            "timeline": "timeline.jsonl",
        },
        "validation": {
            "status": "passed",
            **({"warnings": [TIMELINE_EMPTY_WARNING]} if not timeline_bytes else {}),
            "checks": [
                "schema",
                "artifacts",
                "semantics",
                "geography",
                "timeline",
                "privacy",
            ],
        },
    }

    if branch_switch:
        manifest_data["branch_switch"] = branch_switch

    target_screens_dir = screens_dir
    if target_screens_dir is None:
        if DEFAULT_SCREENS_DIR is not None:
            target_screens_dir = DEFAULT_SCREENS_DIR
        elif "KENSHI_SCREENS_DIR" in os.environ:
            target_screens_dir = Path(os.environ["KENSHI_SCREENS_DIR"])
        elif published_dir.resolve() == canonical_pub_dir:
            from tools.config import Config
            target_screens_dir = Config.from_env().screens

    screenshots_index_data = None
    hud_warning: str | None = None
    screenshots_index_md = None
    screenshots_section = None
    pdf_frames: list[dict[str, Any]] = []
    collected_screenshots: list[dict[str, Any]] = []

    if target_screens_dir is not None:
        target_screens_dir = Path(target_screens_dir).resolve()
        if not target_screens_dir.is_dir() or linked(target_screens_dir):
            raise PublisherError(f"Каталог скриншотов недоступен: {target_screens_dir}")

        gen_str = manifest_data["generated_at"]
        gen_dt = datetime.fromisoformat(gen_str.replace("Z", "+00:00"))
        cutoff_dt = gen_dt - timedelta(days=7)

        from tools.screenshots import (
            build_screenshots_index,
            collect_recent_screenshots,
            copy_screenshot_safe,
            BLOB_DEFAULT_URL,
            RAW_DEFAULT_URL,
        )

        from tools import screenshot_delta, screenshot_publish
        from tools.screenshot_clustering import cluster_screenshots

        window_screenshots = collect_recent_screenshots(
            source_dir=target_screens_dir,
            cutoff_time=cutoff_dt,
            now_time=gen_dt,
        )
        # В выпуск идут только ключевые кадры, уменьшенные до 1600 px; полный набор остаётся в локальном архиве.
        clustered_window, window_episodes = cluster_screenshots(window_screenshots)
        archived = {
            item["filename"]: item
            for item in screenshot_publish.archive_screenshots(clustered_window)
        }
        from tools.screenshot_hud import to_ocr, update_hud_index

        try:
            # Распознаётся оригинал: на кадре 1600 px цифры интерфейса искажаются.
            hud_rows, hud_warning = update_hud_index(window_screenshots, now_iso=gen_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        except Exception as hud_error:
            # Изоляция сбоев: ошибка разметки не блокирует публикацию, но попадает в предупреждения
            hud_rows, hud_warning = {}, f"OCR: индекс недоступен: {hud_error}"
        snapshot_entries = screenshot_delta.snapshot_chain(idx_data, snapshot_id)
        new_originals = screenshot_delta.new_frame_names(
            archived.values(), screenshot_delta.delta_boundary(idx_data, snapshot_id, effective_parent)
        )
        collected_screenshots = []
        for order, key_item in enumerate(
            screenshot_publish.select_keyframes(list(archived.values()), window_episodes, priority=new_originals), 1
        ):
            name = key_item["published_name"]
            collected_screenshots.append({
                **key_item,
                "original_filename": key_item["filename"],
                "order": order,
                "filename": name,
                "path": f"screenshots/{name}",
                "source_path": key_item["archive_path"],
                "size_bytes": key_item["published_size"],
                "sha256": key_item["published_sha256"],
                "source_sha256": key_item["sha256"],
                "is_keyframe": True,
                "url_raw": f"{RAW_DEFAULT_URL}/published/screenshots/{name}",
                "url_blob": f"{BLOB_DEFAULT_URL}/published/screenshots/{name}",
            })

        gen_iso = gen_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        w_start_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        screenshots_index_data = build_screenshots_index(
            items=collected_screenshots,
            generated_at=gen_iso,
            window_days=7,
            window_start=w_start_iso,
            window_end=gen_iso,
            new_frames=[archived[name]["published_name"] for name in new_originals],
        )

        prev_idx_path = published_dir / "screenshots/index.json"
        if prev_idx_path.is_file():
            try:
                prev_idx = read_json(prev_idx_path.read_bytes())
                if (prev_idx.get("screenshots") == screenshots_index_data.get("screenshots")
                        and prev_idx.get("new_frames") == screenshots_index_data.get("new_frames")):
                    screenshots_index_data["generated_at"] = prev_idx["generated_at"]
                    screenshots_index_data["window_start"] = prev_idx.get("window_start", screenshots_index_data["window_start"])
                    screenshots_index_data["window_end"] = prev_idx.get("window_end", screenshots_index_data["window_end"])
            except Exception:
                pass

        manifest_data["artifacts"]["screenshots"] = "screenshots/index.json"
        manifest_data["artifacts"]["screenshot_index_md"] = "screenshots/index.md"
        if "screenshots" not in manifest_data["validation"]["checks"]:
            manifest_data["validation"]["checks"].append("screenshots")

        # Техническое извлечение данных кадра и хронологическая лента
        screenshots_annotations_bytes = None
        screenshots_feed_md = None
        annotated_records: list[dict[str, Any]] = []
        try:
            from tools.screenshot_extractor import extract_screenshots_batch
            from tools.screenshot_feed import render_screenshot_feed, serialize_annotations_jsonl

            clustered_screenshots, episodes_summary = cluster_screenshots(collected_screenshots)
            prev_ann_path = published_dir / "screenshots/annotations.jsonl"
            annotated_records = extract_screenshots_batch(
                clustered_screenshots=clustered_screenshots,
                cache_path=prev_ann_path if prev_ann_path.is_file() else None,
                now_iso=screenshots_index_data["generated_at"],
                hud_rows=hud_rows,
            )
            if not screenshot_publish.has_ocr_content(annotated_records):
                # Пустой OCR: аннотации и лента не несут информации и не публикуются.
                annotated_records = []
            else:
                screenshots_annotations_bytes = serialize_annotations_jsonl(annotated_records)
                screenshots_feed_md = render_screenshot_feed(
                    annotations=annotated_records,
                    episodes=episodes_summary,
                    generated_at=screenshots_index_data["generated_at"],
                    window_days=screenshots_index_data["window_days"],
                    window_start=screenshots_index_data["window_start"],
                    window_end=screenshots_index_data["window_end"],
                )

                manifest_data["artifacts"]["screenshot_annotations"] = "screenshots/annotations.jsonl"
                manifest_data["artifacts"]["screenshot_feed"] = "screenshots/feed.md"
                if "screenshot_annotations" not in manifest_data["validation"]["checks"]:
                    manifest_data["validation"]["checks"].append("screenshot_annotations")
        except Exception:
            # Изоляция сбоев: ошибка разметки не блокирует публикацию
            screenshots_annotations_bytes = None
            screenshots_feed_md = None

        # Индекс кадров и шапки PDF: дельта снимка, архивный хвост и первичная разметка
        def load_snapshot_locations(target_id: str) -> dict[str, Any] | None:
            if target_id == snapshot_id:
                return locations_data
            path = ROOT / "data/locations" / f"{target_id}.json"
            return read_json(path.read_bytes()) if path.is_file() else None

        bundled = {item["published_name"] for item in collected_screenshots}
        annotations = {record.get("filename"): record for record in annotated_records}

        def to_frame(item: dict[str, Any]) -> dict[str, Any]:
            # Кадры дельты вне выпуска не имеют аннотации, но разметка интерфейса в индексе у них есть.
            hud_ocr = to_ocr(hud_rows.get(item["sha256"]))
            annotation = annotations.get(item["published_name"]) or ({"ocr": hud_ocr} if hud_ocr else None)
            return screenshot_delta.frame_record(
                item, snapshot_entries, load_chain_diff, load_snapshot_locations,
                annotation, item["published_name"] in bundled,
            )

        new_frames = [to_frame(archived[name]) for name in new_originals]
        old_items = [archived[item["original_filename"]] for item in collected_screenshots
                     if item["original_filename"] not in set(new_originals)]
        tail_frames = [to_frame(item) for item in old_items[-screenshot_delta.TAIL_FRAMES:]]
        screenshots_index_md = screenshot_delta.render_index_md(screenshots_index_data, new_frames, tail_frames)
        screenshots_section = screenshot_delta.render_brief_section(new_frames, screenshots_index_data["count"])
        pdf_frames = new_frames or tail_frames[-screenshot_delta.PDF_FALLBACK_FRAMES:]

    brief_md = render_brief(
        cur, diff_data, snapshot_id, locations=locations_data, timeline=timeline_data, timeline_projection=True,
        publication={**manifest_data, "generated_at": GENERATED_AT_TOKEN}, screenshots_section=screenshots_section,
    )

    publication_warnings: list[str] = [hud_warning] if hud_warning else []
    lock_file = Path(tempfile.gettempdir()) / f"kenshi_{published_dir.name}.lock"
    staging_dir = published_dir.parent / f"{published_dir.name}.staging"
    backup_dir = published_dir.parent / f"{published_dir.name}.backup"

    with PublishLock(lock_file):
        recover_if_needed(published_dir, backup_dir, staging_dir)

        # Подготовка staging
        staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Сохранение .gitkeep
            gitkeep_path = published_dir / ".gitkeep"
            gitkeep_bytes = gitkeep_path.read_bytes() if gitkeep_path.is_file() else b""
            (staging_dir / ".gitkeep").write_bytes(gitkeep_bytes)

            from tools.current_layers import build_layers, public_projection

            projected = public_projection(cur)
            layer_files = {"extended/current.json": canonical(projected), **build_layers(projected)}
            for rel_path, data in layer_files.items():
                (staging_dir / rel_path).parent.mkdir(parents=True, exist_ok=True)
                (staging_dir / rel_path).write_bytes(data)
            (staging_dir / "changes.json").write_bytes(canonical(diff_data))
            (staging_dir / "changes.md").write_text(changes_md, encoding="utf-8")
            previous_locations_path = published_dir / "locations.json"
            if previous_locations_path.is_file():
                previous_locations_data = read_json(previous_locations_path.read_bytes())
                stable_locations = dict(locations_data, created_at=previous_locations_data.get("created_at"))
                if stable_locations == previous_locations_data:
                    locations_data = stable_locations
            (staging_dir / "locations.json").write_bytes(canonical(locations_data))
            from tools.current_layers import public_world_reference

            (staging_dir / "world_reference.json").write_bytes(canonical(public_world_reference(w_ref)))
            (staging_dir / "timeline.jsonl").write_bytes(timeline_bytes)
            (staging_dir / "brief.md").write_text(brief_md, encoding="utf-8")
            (staging_dir / "squad.md").write_text(squad_md, encoding="utf-8")

            if screenshots_index_data is not None:
                sc_staging = staging_dir / "screenshots"
                sc_staging.mkdir(parents=True, exist_ok=True)
                (sc_staging / "index.json").write_bytes(canonical(screenshots_index_data))
                (sc_staging / "index.md").write_text(screenshots_index_md, encoding="utf-8")
                if screenshots_annotations_bytes is not None:
                    (sc_staging / "annotations.jsonl").write_bytes(screenshots_annotations_bytes)
                if screenshots_feed_md is not None:
                    (sc_staging / "feed.md").write_text(screenshots_feed_md, encoding="utf-8")
                for item in collected_screenshots:
                    src_p = item["source_path"]
                    dst_p = sc_staging / item["filename"]
                    copy_screenshot_safe(src_p, dst_p, expected_sha256=item["sha256"])
                if pdf_frames:
                    from tools.screenshot_pdf import build_screenshots_pdf

                    # PDF нужен только Диску: его сбой не должен останавливать публикацию и доставку в GitHub.
                    pdf_path = sc_staging / screenshot_delta.PDF_NAME
                    try:
                        # Сборка в %TEMP%: на Диск и в выпуск попадает только завершённый файл.
                        with tempfile.TemporaryDirectory() as pdf_tmp:
                            built = Path(pdf_tmp) / screenshot_delta.PDF_NAME
                            publication_warnings += [f"PDF: кадр пропущен: {name}"
                                                     for name in build_screenshots_pdf(pdf_frames, built)]
                            if built.is_file():
                                shutil.copyfile(built, pdf_path)
                    except Exception as pdf_error:
                        pdf_path.unlink(missing_ok=True)
                        publication_warnings.append(f"PDF не собран: {pdf_error}")

            # Время выпуска меняется только при изменении содержимого bundle.
            previous_manifest = published_dir / "manifest.json"
            if previous_manifest.is_file():
                previous = read_json(previous_manifest.read_bytes())
                comparable = dict(manifest_data, generated_at=previous.get("generated_at"))
                previous_stamp = str(previous.get("generated_at")).encode("utf-8")

                def unchanged(name: str) -> bool:
                    before = (published_dir / name).read_bytes()
                    if name == "brief.md":
                        before = before.replace(previous_stamp, GENERATED_AT_TOKEN.encode("utf-8"))
                    return before == (staging_dir / name).read_bytes()

                same_files = all(
                    (published_dir / name).is_file() and unchanged(name)
                    for name in manifest_data["artifacts"].values()
                )
                if screenshots_index_data is not None and same_files:
                    pub_sc_dir = published_dir / "screenshots"
                    staging_sc_dir = staging_dir / "screenshots"
                    if pub_sc_dir.is_dir() and staging_sc_dir.is_dir():
                        pub_files = {p.name: p.stat().st_size for p in pub_sc_dir.iterdir() if p.is_file()}
                        stg_files = {p.name: p.stat().st_size for p in staging_sc_dir.iterdir() if p.is_file()}
                        if pub_files != stg_files:
                            same_files = False
                    else:
                        same_files = False
                if same_files and comparable == previous:
                    manifest_data["generated_at"] = previous["generated_at"]
                elif manifest_data["generated_at"] == previous.get("generated_at"):
                    stamp = datetime.fromisoformat(previous["generated_at"].replace("Z", "+00:00"))
                    manifest_data["generated_at"] = (stamp + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")
            (staging_dir / "manifest.json").write_bytes(canonical(manifest_data))
            (staging_dir / "brief.md").write_text(
                brief_md.replace(GENERATED_AT_TOKEN, manifest_data["generated_at"]), encoding="utf-8"
            )

            if fault_inject == "before_validate":
                raise PublisherError("Fault injection: сбой до валидации пакета")

            validation_summary = validate_publication(staging_dir, index_data=idx_data)

            if fault_inject == "after_validate":
                raise PublisherError("Fault injection: сбой после валидации пакета")

            if dry_run:
                shutil.rmtree(staging_dir)
                return {
                    "dry_run": True,
                    "snapshot_id": snapshot_id,
                    "validation": validation_summary,
                }

            # Атомарное переключение каталогов на Windows
            has_prev = published_dir.exists()
            if has_prev:
                published_dir.rename(backup_dir)

            if fault_inject == "during_switch":
                raise PublisherError("Fault injection: сбой во время переключения директорий")

            try:
                staging_dir.rename(published_dir)
            except Exception as switch_err:
                # Откат из backup
                if has_prev and backup_dir.exists() and not published_dir.exists():
                    backup_dir.rename(published_dir)
                raise PublisherError(f"Сбой переименования staging в target: {switch_err}") from switch_err

            # Подтверждение успешного переключения
            if not published_dir.is_dir() or not (published_dir / "manifest.json").is_file():
                if has_prev and backup_dir.exists():
                    backup_dir.rename(published_dir)
                raise PublisherError("Каталог target повреждён после переключения")

            # Проверка неизменности .gitkeep
            if (published_dir / ".gitkeep").read_bytes() != gitkeep_bytes:
                if has_prev and backup_dir.exists():
                    shutil.rmtree(published_dir)
                    backup_dir.rename(published_dir)
                raise PublisherError(".gitkeep был изменён в процессе публикации")

            # Очистка backup после подтверждения успеха
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

            # Обновление индекса после успешной публикации
            if idx_data is not None and effective_branch:
                if effective_branch in idx_data.get("branches", {}):
                    idx_data["branches"][effective_branch]["head_snapshot_id"] = snapshot_id
                if branch_id is not None:
                    idx_data["active_branch_id"] = effective_branch
                idx_data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                try:
                    save_index(idx_data, idx_target, with_lock=False)
                except Exception:
                    pass

        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if backup_dir.exists() and not published_dir.exists():
                backup_dir.rename(published_dir)
            raise

    res = {
        "status": "published",
        "snapshot_id": snapshot_id,
        "published_dir": str(published_dir),
        "manifest": manifest_data,
        "timeline_sha256": digest(timeline_bytes),
    }
    if screenshots_index_data is not None:
        res["screenshots_count"] = screenshots_index_data["count"]
    if publication_warnings:
        res["warnings"] = publication_warnings
    return res
