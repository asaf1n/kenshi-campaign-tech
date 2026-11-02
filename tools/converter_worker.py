"""Точка запуска нативного конвертера Kenshi (строго через python -I -B)."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import gamedb, kenshi_gd, saves, session_export
from tools.io_utils import ExtractionError, canonical, digest, file_hash, install_write_guard, read_json
from tools.normalization import scrub


ENGINE_FILES: list[str] = [
    "tools/converter_worker.py",
    "tools/export_platoons.py",
    "tools/gamedb.py",
    "tools/itemstats.py",
    "tools/kenshi_gd.py",
    "tools/player_assets.py",
    "tools/prosthetics.py",
    "tools/save_reader.py",
    "tools/saves.py",
    "tools/session_diff.py",
    "tools/session_export.py",
    "tools/task_types.py",
    "tools/zipnames.py",
]


def strict_inputs(save: Path, faction: str) -> None:
    quick_path = save / "quick.save"
    if not quick_path.is_file():
        raise ExtractionError("Отсутствует обязательный quick.save")
    quick = kenshi_gd.read_file(quick_path.read_bytes())
    if quick["consumed"] <= 0 or not any(r["type"] == 56 for r in quick["records"]):
        raise ExtractionError("Повреждена обязательная структура quick.save")
    registry = saves.read_quick_save(str(save))["registry"]
    platoon = save / "platoon"
    if not platoon.is_dir():
        raise ExtractionError("Отсутствует обязательный каталог platoon")
    for stem, item in sorted(registry.items()):
        if Path(stem).name != stem or "/" in stem or "\\" in stem or ":" in stem:
            raise ExtractionError("Некорректный путь platoon в реестре")
        path = platoon / (stem + ".platoon")
        if item.get("faction_sid") == faction and not path.is_file():
            raise ExtractionError("Отсутствует обязательный .platoon игрока")
        # Проверяем активные файлы, в том числе без строки faction_sid
        if path.is_file():
            parsed = kenshi_gd.read_file(path.read_bytes())
            if parsed["consumed"] != parsed["size"] or parsed["header"]["count"] < 0:
                raise ExtractionError("Повреждена обязательная структура .platoon")


def run(request: dict[str, Any]) -> dict[str, Any]:
    if not (sys.flags.isolated and sys.dont_write_bytecode):
        raise ExtractionError("Воркер должен запускаться в изолированном режиме (-I -B)")

    workspace = Path(request["workspace"]).resolve()
    save = Path(request["source"]).resolve()
    game = Path(request["game"]).resolve()
    workshop = Path(request["workshop"]).resolve()
    faction_sid = request["faction_sid"]

    os.environ["KENSHI_DIR"] = str(game)
    os.environ["KENSHI_WORKSHOP_DIR"] = str(workshop)
    gamedb.KENSHI = str(game)
    gamedb.WORKSHOP = str(workshop)
    gamedb.PO_RU = f"{game}/locale/ru_RU/gamedata.po"
    session_export.gamedb.KENSHI = str(game)
    session_export.gamedb.WORKSHOP = str(workshop)
    session_export.gamedb.PO_RU = f"{game}/locale/ru_RU/gamedata.po"
    sys.pycache_prefix = str(workspace / "pycache")
    denied = install_write_guard(workspace)

    cache_dir = workspace / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    session_export.CACHE_DIR = str(cache_dir)

    strict_inputs(save, faction_sid)

    original_load = session_export.gamedb.load_index
    inputs: list[tuple[Path, str]] = []
    mod_files: list[list[str]] = []

    def load_index(cache_dir_arg: Any, files: Any = None, po_path: Any = None) -> Any:
        if files is None:
            raise ExtractionError("Конвертер не передал активные моды")
        target_po = Path(po_path) if po_path else Path(session_export.gamedb.PO_RU)
        for filename in [*files, target_po]:
            path = Path(filename).resolve()
            if path.is_file():
                checksum = file_hash(path)
                inputs.append((path, checksum))
                mod_files.append([path.name, checksum])
        return original_load(cache_dir_arg, files, po_path=po_path)

    session_export.gamedb.load_index = load_index
    session_export.collect_stable = session_export.collect

    snapshot = session_export.build_snapshot(str(save), faction_sid, session_export.Timer())

    if denied:
        raise ExtractionError("Конвертер пытался записать защищённые данные")
    if any(file_hash(path) != checksum for path, checksum in inputs):
        raise ExtractionError("Данные игры изменились во время извлечения")

    gd = snapshot.get("meta", {}).get("game_data", {})
    mods = {
        "files": mod_files,
        "mods": gd.get("mods"),
        "missing": gd.get("missing"),
        "mods_source": gd.get("mods_source"),
    }

    return {
        "snapshot": snapshot,
        "converter_files": ENGINE_FILES,
        "mods": mods,
        "mods_sha256": digest(canonical(mods)),
    }


def main() -> int:
    try:
        request = read_json(sys.stdin.buffer.read())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run(request)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
