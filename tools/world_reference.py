"""Сборка, сохранение, загрузка и подбор справочника мира Kenshi."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from tools.config import Config
from tools.io_utils import ExtractionError, canonical, file_hash, read_json
from tools.normalization import scrub
from tools.validation import validate_world_reference

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORLD_DIR = ROOT / "data/world"


def build_world_reference(
    config: Config | None = None,
    mods_override: list[str] | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    cfg = config or Config.from_env()

    def _execute(work_dir: Path) -> dict[str, Any]:
        request = {
            "converter": str(cfg.converter.resolve()),
            "workspace": str(work_dir.resolve()),
            "game": str(cfg.game.resolve()),
            "workshop": str(cfg.workshop.resolve()),
            "mods_override": mods_override,
        }
        worker_path = ROOT / "tools/world_worker.py"
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(worker_path)],
            input=canonical(request),
            capture_output=True,
            cwd=ROOT,
            timeout=600,
        )
        if completed.returncode != 0:
            err = scrub(completed.stderr.decode("utf-8", errors="replace")).strip()
            raise ExtractionError(f"Сборщик справочника мира отказал: {err}")
        world_ref = read_json(completed.stdout)
        validate_world_reference(world_ref)
        return world_ref

    if workspace is not None:
        workspace.mkdir(parents=True, exist_ok=True)
        return _execute(workspace)

    with tempfile.TemporaryDirectory(prefix=".world-build-", dir=ROOT) as temp_dir:
        return _execute(Path(temp_dir))


def save_world_reference(
    world_ref: dict[str, Any],
    output: Path | None = None,
) -> Path:
    validate_world_reference(world_ref)
    world_ref_id = world_ref["world_ref_id"]

    out_path = output or DEFAULT_WORLD_DIR
    if out_path.is_dir() or not out_path.suffix:
        target = out_path / f"{world_ref_id}.json"
    else:
        target = out_path

    target.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(world_ref)

    if target.exists():
        if target.is_file() and target.read_bytes() == raw:
            return target.resolve()
        raise ExtractionError(
            f"Существующий справочник мира {target.name} имеет другое содержимое"
        )

    # Атомарная запись через временный файл рядом с целевым
    tmp_path = target.with_name(f".tmp-{target.name}")
    try:
        tmp_path.write_bytes(raw)
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return target.resolve()


def load_world_reference(
    target: str | Path,
    world_dir: Path | None = None,
) -> dict[str, Any]:
    p = Path(target)
    candidates: list[Path] = []
    if p.is_file():
        candidates.append(p)
    else:
        base_dir = world_dir or DEFAULT_WORLD_DIR
        candidates.append(base_dir / f"{target}.json")
        candidates.append(base_dir / target)

    for cand in candidates:
        if cand.is_file():
            data = read_json(cand.read_bytes())
            validate_world_reference(data)
            return data

    raise FileNotFoundError(f"Справочник мира не найден: {target}")


def find_world_reference(
    mods: list[str] | None = None,
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
) -> tuple[Path | None, list[str]]:
    base_dir = world_dir or DEFAULT_WORLD_DIR
    warnings: list[str] = []

    if world_ref_id is not None:
        ref_file = base_dir / f"{world_ref_id}.json"
        if not ref_file.is_file():
            ref_file = base_dir / world_ref_id
        if ref_file.is_file():
            data = read_json(ref_file.read_bytes())
            if mods is not None and data.get("mods") != mods:
                warnings.append(
                    f"world_reference_mismatch: список модов {mods} не совпадает с {data.get('mods')}"
                )
            return ref_file.resolve(), warnings
        warnings.append(f"world_reference_mismatch: справочник {world_ref_id} не найден")
        return None, warnings

    if mods is not None:
        matching: list[Path] = []
        if base_dir.is_dir():
            for p in sorted(base_dir.glob("*.json")):
                if p.name.startswith("."):
                    continue
                try:
                    data = read_json(p.read_bytes())
                    if data.get("format") == "kenshi-world-reference/1" and data.get("mods") == mods:
                        matching.append(p)
                except Exception:
                    continue

        if len(matching) == 1:
            return matching[0].resolve(), []
        if len(matching) > 1:
            warnings.append(
                f"world_reference_mismatch: неоднозначное совпадение модов ({len(matching)} справочников)"
            )
            return None, warnings
        warnings.append("world_reference_mismatch: подходящий справочник мира не найден")
        return None, warnings

    warnings.append("world_reference_mismatch: не указаны ни world_ref_id, ни список mods")
    return None, warnings
