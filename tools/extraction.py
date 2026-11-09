"""Извлечение и регистрация неизменяемого снимка."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from tools.config import Config
from tools.io_utils import ExtractionError, canonical, digest, file_hash, linked, outside, read_json, tree_hashes
from tools.normalization import normalize, scrub

ROOT = Path(__file__).resolve().parents[1]


from tools.converter_worker import ENGINE_FILES


def engine_hashes() -> dict[str, str]:
    return {p: file_hash(ROOT / p) for p in ENGINE_FILES}


def transform_hash() -> str:
    return digest(canonical([[p.name, file_hash(p)] for p in sorted((ROOT / "tools").glob("*.py"))]))


PROVENANCE_FORMAT = "kenshi-snapshot/2"


def snapshot_identity(source_kind: str, source_sha256: str, faction_sid: str, converter_sha256: str | None,
                      mods: Any) -> dict[str, Any]:
    """Поля идентичности снимка. Кампания, родитель и прочие внешние значения сюда не входят."""
    return {"source_kind": source_kind, "source_sha256": source_sha256, "faction_sid": faction_sid,
            "converter_sha256": converter_sha256, "mods_sha256": digest(canonical(mods)),
            "transform_sha256": transform_hash()}


def convert_save(source: Path, config: Config, workspace: Path) -> dict[str, Any]:
    before = engine_hashes()
    request: dict[str, Any] = {"source": str(source), "workspace": str(workspace),
                               "game": str(config.game.resolve()), "workshop": str(config.workshop.resolve()),
                               "faction_sid": config.faction_sid}
    completed = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools/converter_worker.py")],
                               input=canonical(request), capture_output=True, cwd=workspace, timeout=600)
    after = engine_hashes()
    if before != after:
        raise ExtractionError("Исходники нативного движка изменились во время извлечения")
    if completed.returncode:
        raise ExtractionError("Конвертер отказал: " + str(scrub(completed.stderr.decode("utf-8", errors="replace"))).strip())
    response = read_json(completed.stdout)
    pairs = [[name, before[name]] for name in sorted(response["converter_files"])]
    if not pairs:
        raise ExtractionError("Нет отпечатка использованных исходников конвертера")
    response["converter"] = {"files": pairs, "sha256": digest(canonical(pairs))}
    return response


def store_snapshot(output: Path, snapshot_id: str, current: bytes, provenance: bytes) -> None:
    target = output / snapshot_id
    expected = {"current.json": current, "provenance.json": provenance}
    if target.exists():
        if linked(target) or not target.is_dir() or {p.name for p in target.iterdir()} != set(expected):
            raise ExtractionError("Повреждён существующий снимок")
        if any(linked(target / name) or (target / name).read_bytes() != data for name, data in expected.items()):
            raise ExtractionError("Существующий snapshot_id имеет другое содержимое")
        return
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=output) as staging:
        stage = Path(staging) / "ready"
        stage.mkdir()
        for name, data in expected.items():
            with (stage / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Непустой target никогда не заменяется; гонка writer приводит к отказу.
        stage.rename(target)


def extract(source: Path, source_kind: str, config: Config, output: Path | None = None) -> dict[str, Any]:
    """Неизменяемый снимок: идентичность — содержимое источника и параметры разбора, без внешнего контекста."""
    source = source.resolve(strict=True)
    if source_kind not in {"save", "export"}:
        raise ExtractionError("Неизвестный source_kind")
    output = outside(output or ROOT / "data/snapshots", [source, *config.protected()])
    if source_kind == "save":
        if not source.is_dir() or not (source / "quick.save").is_file():
            raise ExtractionError("Отсутствует обязательный quick.save")
        before: Any = tree_hashes(source)
        source_sha = digest(canonical(before))
    else:
        raw = source.read_bytes()
        before = digest(raw)
        source_sha = before
        snapshot = read_json(raw)
        # Отказ до создания каталогов и без обращения к конвертеру/игре.
        normalize(snapshot)
    output.mkdir(parents=True, exist_ok=True)
    converter = None
    mods = None
    try:
        if source_kind == "save":
            with tempfile.TemporaryDirectory(prefix=".extract-", dir=output) as job:
                response = convert_save(source, config, Path(job))
            snapshot = response["snapshot"]
            converter, mods = response["converter"], response["mods"]
        current, diagnostics = normalize(snapshot)
        exported_faction = current["meta"].get("player_faction", {}).get("sid")
        if exported_faction is not None and exported_faction != config.faction_sid:
            raise ExtractionError("Фракция экспорта не совпадает с эффективным faction_sid")
        if source_kind == "export":
            mods = {"status": "export_metadata_only", "metadata": current["meta"].get("game_data")}
            diagnostics["limitations"].append("export: байтовые версии исходного конвертера и модов не установлены")
        identity = snapshot_identity(source_kind, source_sha, config.faction_sid,
                                     converter["sha256"] if converter else None, mods)
        snapshot_id = "sha256-" + digest(canonical(identity))
        current_bytes = canonical(current)
        provenance = {"format": PROVENANCE_FORMAT, "snapshot_id": snapshot_id, **identity,
                      "source_format": current["format"], "converter": converter, "mods": scrub(mods),
                      "source_files": before if source_kind == "save" else None,
                      "game_time": {k: current["world"].get(k) for k in ("day", "time")},
                      "current_sha256": digest(current_bytes), "diagnostics": diagnostics,
                      "source_verified_unchanged": True}
        provenance_bytes = canonical(scrub(provenance))
    finally:
        after = tree_hashes(source) if source_kind == "save" else file_hash(source)
        if before != after:
            raise ExtractionError("Источник изменился во время извлечения; снимок не зарегистрирован")
    store_snapshot(output, snapshot_id, current_bytes, provenance_bytes)
    return {"snapshot_id": snapshot_id, "source_sha256": source_sha,
            "current_sha256": digest(current_bytes), "diagnostics": diagnostics}
