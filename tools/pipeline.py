"""Сквозной вертикальный срез конвейера: extract -> diff -> resolve_locations -> render -> validate -> publish."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.config import Config
from tools.extraction import ROOT, extract
from tools.index import register_snapshot
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
    branch_id: str | None = None,
    parent_snapshot_id: str | None = None,
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    index_path: Path | None = None,
    dry_run: bool = False,
    timeline_path: Path | None = None,
    sessions_root: Path | None = None,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    snapshots_dir = output_snapshots or ROOT / "data/snapshots"
    diffs_dir = output_diffs or ROOT / "data/diffs"
    pub_dir = published_dir or ROOT / "published"
    idx_path = index_path or (snapshots_dir.parent / "index.json")
    effective_campaign = campaign_id or "hot-lon"

    extract_res = extract(
        source=source,
        source_kind=source_kind,
        config=config,
        output=snapshots_dir,
        campaign_id=effective_campaign,
        branch_id=branch_id,
        parent_snapshot_id=parent_snapshot_id,
    )

    snapshot_id = extract_res["snapshot_id"]

    prov = read_json((snapshots_dir / snapshot_id / "provenance.json").read_bytes())
    index_res = register_snapshot(
        snapshot_id=snapshot_id,
        parent_snapshot_id=parent_snapshot_id or prov.get("parent_snapshot_id"),
        branch_id=branch_id or prov.get("branch_id"),
        campaign_id=effective_campaign or prov.get("campaign_id"),
        game_time=prov.get("game_time"),
        world_ref_id=world_ref_id or prov.get("world_ref_id"),
        source_kind=source_kind,
        source_sha256=prov.get("source_sha256", ""),
        index_path=idx_path,
    )

    publish_res = publish(
        snapshot_id=snapshot_id,
        snapshots_dir=snapshots_dir,
        diffs_dir=diffs_dir,
        published_dir=pub_dir,
        parent_snapshot_id=parent_snapshot_id,
        branch_id=branch_id,
        campaign_id=effective_campaign,
        world_ref_id=world_ref_id,
        world_dir=world_dir,
        index_path=idx_path,
        dry_run=dry_run,
        timeline_path=timeline_path,
        sessions_root=sessions_root,
        screens_dir=screens_dir if screens_dir is not None else config.screens,
    )

    return {
        "pipeline_status": "success",
        "snapshot_id": snapshot_id,
        "extract": extract_res,
        "index": index_res,
        "publish": publish_res,
    }
