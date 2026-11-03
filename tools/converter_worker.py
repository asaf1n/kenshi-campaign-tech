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

from tools import gamedb, kenshi_gd, save_reader, saves, session_export
from tools.combat_analysis import calculate_character_combat, calculate_character_physics
from tools.io_utils import ExtractionError, canonical, digest, file_hash, install_write_guard, read_json
from tools.normalization import scrub


ENGINE_FILES: list[str] = [
    "tools/combat_analysis.py",
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


def extend_buildings(buildings: list[dict[str, Any]], native: list[dict[str, Any]]) -> None:
    """Добавляет производственные поля, сохраняя прежние storage и иерархию."""
    def indexed(rows: list[dict[str, Any]], zone: str | None = None) -> dict[tuple[Any, Any], dict[str, Any]]:
        result: dict[tuple[Any, Any], dict[str, Any]] = {}
        for row in rows:
            row_zone = row.get("zone", zone)
            key = (row_zone, row["id"])
            descendants = indexed(row.get("interior", []), row_zone)
            if key in result or key in descendants or result.keys() & descendants.keys():
                raise ExtractionError("Повтор идентификатора здания в зоне")
            result[key] = row
            result.update(descendants)
        return result

    previous, additions = indexed(buildings), indexed(native)
    if previous.keys() != additions.keys():
        raise ExtractionError("Состав зданий различается между чтением основных и расширенных полей")
    for key, building in previous.items():
        building.update({field: value for field, value in additions[key].items()
                         if field not in building and field != "interior"})


def build_native_snapshot(save: Path, faction_sid: str, game_index: dict[str, list[Any]]) -> dict[str, Any]:
    """Сохраняет представление v10 и дополняет его нативными полями v11."""
    original_character = session_export.character
    original_compact = session_export.compact_character
    fields = ("jobs", "carrying", "bounties", "combat_modes", "items")

    def character(inst: dict[str, Any], by: dict[str, Any], names: Any) -> dict[str, Any]:
        result = original_character(inst, by, names)
        if result.get("faction", {}).get("sid") == faction_sid:
            native = save_reader.parse_character(inst, by, {}, gamedb_inst=game_index)
            if native is None:
                raise ExtractionError("Не получены расширенные поля персонажа игрока")
            result["session11"] = {field: native[field] for field in fields}
        return result

    def compact_character(ch: dict[str, Any], squad: dict[str, Any], names: Any,
                          const: dict[str, Any], warnings: list[str],
                          limbs: tuple[dict[str, dict[str, Any]], list[str]]) -> dict[str, Any]:
        base = original_compact(ch, squad, names, const, warnings, limbs)
        s11 = ch.get("session11", {})
        merged = base | s11
        if s11:
            merged["physics_calculated"] = calculate_character_physics(merged)
            merged["combat_calculated"] = calculate_character_combat(merged)
        return merged

    # Адаптация действует только внутри изолированного воркера. Поля относятся к
    # одному inst/by: различия запасных ID и одноимённые персонажи не требуют join.
    session_export.character = character
    session_export.compact_character = compact_character
    try:
        snapshot = session_export.build_snapshot(str(save), faction_sid, session_export.Timer())
    finally:
        session_export.character = original_character
        session_export.compact_character = original_compact

    quick = save_reader.read_quick_save(save, faction_sid, game_index)
    native_property = save_reader.read_player_property(
        save, faction_sid, quick.get("towns"), game_index, snapshot["warnings"],
    )
    extend_buildings(snapshot["property"]["buildings"], native_property["buildings"])
    snapshot.update(format="kenshi-session/11", diplomacy=quick["diplomacy"], world_state=quick["world_state"])
    return snapshot


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
    game_index: dict[str, list[Any]] = {}

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
        index, cache = original_load(cache_dir_arg, files, po_path=po_path)
        game_index.update(index)
        return index, cache

    session_export.gamedb.load_index = load_index
    original_collect_stable = session_export.collect_stable
    session_export.collect_stable = session_export.collect

    try:
        snapshot = build_native_snapshot(save, faction_sid, game_index)
    finally:
        session_export.gamedb.load_index = original_load
        session_export.collect_stable = original_collect_stable

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
