"""Сквозной вертикальный срез конвейера: extract -> diff -> validate -> render -> publish."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.config import Config
from tools.extraction import ROOT, extract
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
    dry_run: bool = False,
) -> dict[str, Any]:
    snapshots_dir = output_snapshots or ROOT / "data/snapshots"
    diffs_dir = output_diffs or ROOT / "data/diffs"
    pub_dir = published_dir or ROOT / "published"

    extract_res = extract(
        source=source,
        source_kind=source_kind,
        config=config,
        output=snapshots_dir,
        campaign_id=campaign_id,
        branch_id=branch_id,
        parent_snapshot_id=parent_snapshot_id,
    )

    snapshot_id = extract_res["snapshot_id"]

    publish_res = publish(
        snapshot_id=snapshot_id,
        snapshots_dir=snapshots_dir,
        diffs_dir=diffs_dir,
        published_dir=pub_dir,
        parent_snapshot_id=parent_snapshot_id,
        branch_id=branch_id,
        campaign_id=campaign_id,
        dry_run=dry_run,
    )

    return {
        "pipeline_status": "success",
        "snapshot_id": snapshot_id,
        "extract": extract_res,
        "publish": publish_res,
    }
