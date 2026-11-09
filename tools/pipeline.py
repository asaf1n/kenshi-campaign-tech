"""Сквозной вертикальный срез конвейера: extract -> diff -> resolve_locations -> render -> validate -> publish."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from tools.campaign_check import MIN_SHARED_IDS, MIN_SHARED_SHARE, compare_with_parent, require_same_campaign, roster
from tools.config import Config
from tools.extraction import ROOT, extract
from tools.index import campaign_head, load_index, parent_for, register_snapshot
from tools.io_utils import read_json
from tools.publisher import publish


def run_pipeline(
    source: Path,
    source_kind: str,
    config: Config,
    output_snapshots: Path | None = None,
    output_diffs: Path | None = None,
    published_dir: Path | None = None,
    campaign_id: str | None = None,
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    index_path: Path | None = None,
    dry_run: bool = False,
    timeline_path: Path | None = None,
    sessions_root: Path | None = None,
    screens_dir: Path | None = None,
    allow_new_campaign: bool = False,
) -> dict[str, Any]:
    snapshots_dir = output_snapshots or ROOT / "data/snapshots"
    diffs_dir = output_diffs or ROOT / "data/diffs"
    pub_dir = published_dir or ROOT / "published"
    idx_path = index_path or (snapshots_dir.parent / "index.json")
    effective_campaign = campaign_id or "hot-lon"

    extract_res = extract(source=source, source_kind=source_kind, config=config, output=snapshots_dir)
    snapshot_id = extract_res["snapshot_id"]

    # Родитель — голова кампании в индексе (без снимков того же сейва); оператор его не выбирает.
    index_before = load_index(idx_path)
    known = index_before.get("snapshots", {}).get(snapshot_id)
    parent_snapshot_id = (known.get("parent_snapshot_id") if known is not None
                          else parent_for(index_before, effective_campaign, extract_res.get("source_sha256")))

    # До регистрации в индексе и публикации: чужой сейв не должен попасть в published/.
    campaign_check = _check_campaign(
        snapshots_dir, snapshot_id, parent_snapshot_id, effective_campaign, allow_new_campaign)

    prov = read_json((snapshots_dir / snapshot_id / "provenance.json").read_bytes())
    index_res = register_snapshot(
        snapshot_id=snapshot_id,
        campaign_id=effective_campaign,
        game_time=prov.get("game_time"),
        world_ref_id=world_ref_id,
        source_kind=source_kind,
        source_sha256=prov.get("source_sha256", ""),
        index_path=idx_path,
    )

    # Повтор старого снимка (не головы) не публикуется: выпуск откатился бы по времени без признака перезагрузки.
    if not index_res["is_head"]:
        return {
            "pipeline_status": "skipped",
            "reason": "снимок уже зарегистрирован и не является головой кампании; публикация и доставка не выполняются",
            "snapshot_id": snapshot_id,
            "campaign_check": campaign_check,
            "extract": extract_res,
            "index": index_res,
        }

    publish_res = publish(
        snapshot_id=snapshot_id,
        snapshots_dir=snapshots_dir,
        diffs_dir=diffs_dir,
        published_dir=pub_dir,
        campaign_id=effective_campaign,
        world_ref_id=world_ref_id,
        world_dir=world_dir,
        index_path=idx_path,
        dry_run=dry_run,
        timeline_path=timeline_path,
        sessions_root=sessions_root,
        screens_dir=screens_dir if screens_dir is not None else (config.screens if config.screens.is_dir() else None),
    )

    return {
        "pipeline_status": "success",
        "snapshot_id": snapshot_id,
        "campaign_check": campaign_check,
        "extract": extract_res,
        "index": index_res,
        "publish": publish_res,
    }


def check_save(
    source: Path, config: Config, campaign_id: str | None = None,
    index_path: Path | None = None, snapshots_dir: Path | None = None,
) -> dict[str, Any]:
    """Неизменяющая проверка принадлежности сейва: извлечение во временный каталог, сравнение с головой
    кампании из индекса. data/, индекс и published/ не затрагиваются (индекс только читается)."""
    effective_campaign = campaign_id or "hot-lon"
    parents_dir = snapshots_dir or ROOT / "data/snapshots"
    parent_id = campaign_head(load_index(index_path), effective_campaign)
    with tempfile.TemporaryDirectory(prefix="check-save-") as tmp:
        extract_res = extract(source=source, source_kind="save", config=config, output=Path(tmp))
        candidate = read_json((Path(tmp) / extract_res["snapshot_id"] / "current.json").read_bytes())
    base = {"campaign_id": effective_campaign, "parent_snapshot_id": parent_id,
            "min_shared_share": MIN_SHARED_SHARE, "min_shared_ids": MIN_SHARED_IDS}
    parent_file = parents_dir / parent_id / "current.json" if parent_id else None
    if parent_file is None or not parent_file.is_file():
        reason = ("нет родительского снимка: первая публикация кампании" if parent_id is None
                  else "родительский снимок отсутствует в каталоге снимков")
        return {**base, "status": "skipped", "belongs": True, "reason": reason}
    parent = read_json(parent_file.read_bytes())
    if not roster(parent):
        return {**base, "status": "skipped", "belongs": True,
                "reason": "в родительском снимке нет персонажей с устойчивым id"}
    return {**base, "status": "checked", **compare_with_parent(parent, candidate)}


def _check_campaign(
    snapshots_dir: Path, snapshot_id: str, parent_snapshot_id: str | None,
    campaign_id: str, allow_new_campaign: bool,
) -> dict[str, Any]:
    if allow_new_campaign:
        return {"status": "skipped", "reason": "задан --allow-new-campaign"}
    if not parent_snapshot_id:
        return {"status": "skipped", "reason": "нет родительского снимка: первая публикация кампании"}
    parent_file = snapshots_dir / parent_snapshot_id / "current.json"
    if not parent_file.is_file():
        return {"status": "skipped", "reason": "родительский снимок отсутствует в каталоге снимков"}
    result = require_same_campaign(
        read_json(parent_file.read_bytes()),
        read_json((snapshots_dir / snapshot_id / "current.json").read_bytes()),
        campaign_id,
    )
    return result if result.get("status") == "skipped" else {"status": "passed", **result}
