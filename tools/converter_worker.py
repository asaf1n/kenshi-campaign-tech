"""Единственная точка запуска внешнего конвертера, только через python -I -B."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.io_utils import ExtractionError, canonical, digest, file_hash, install_write_guard, read_json
from tools.normalization import scrub


def strict_inputs(exporter: Any, save: Path, faction: str) -> None:
    quick = exporter.read_file((save / "quick.save").read_bytes())
    if quick["consumed"] <= 0 or not any(r["type"] == 56 for r in quick["records"]):
        raise ExtractionError("Повреждена обязательная структура quick.save")
    registry = exporter.saves.read_quick_save(str(save))["registry"]
    platoon = save / "platoon"
    if not platoon.is_dir():
        raise ExtractionError("Отсутствует обязательный каталог platoon")
    for stem, item in sorted(registry.items()):
        if Path(stem).name != stem or "/" in stem or "\\" in stem or ":" in stem:
            raise ExtractionError("Некорректный путь platoon в реестре")
        path = platoon / (stem + ".platoon")
        if item.get("faction_sid") == faction and not path.is_file():
            raise ExtractionError("Отсутствует обязательный .platoon игрока")
        # Проверяем активные файлы, в том числе без строки faction_sid:
        # бинарный prefilter legacy-конвертера иначе скрывает повреждения.
        if path.is_file():
            parsed = exporter.read_file(path.read_bytes())
            if parsed["consumed"] != parsed["size"] or parsed["header"]["count"] < 0:
                raise ExtractionError("Повреждена обязательная структура .platoon")


def run(request: dict[str, Any]) -> dict[str, Any]:
    converter = Path(request["converter"]).resolve()
    workspace = Path(request["workspace"]).resolve()
    save = Path(request["source"]).resolve()
    os.environ["KENSHI_DIR"] = request["game"]
    os.environ["KENSHI_WORKSHOP_DIR"] = request["workshop"]
    sys.pycache_prefix = str(workspace / "pycache")
    denied = install_write_guard(workspace)
    sys.path.insert(0, str(converter / "tools"))
    import session_export as exporter

    exporter.CACHE_DIR = str(workspace / "cache")
    strict_inputs(exporter, save, request["faction_sid"])
    original_load = exporter.gamedb.load_index
    inputs: list[tuple[Path, str]] = []
    mod_files: list[list[str]] = []

    def load_index(cache_dir: str, files: list[str] | None = None) -> Any:
        if files is None:
            raise ExtractionError("Конвертер не передал активные моды")
        for filename in [*files, exporter.gamedb.PO_RU]:
            path = Path(filename).resolve()
            checksum = file_hash(path)
            inputs.append((path, checksum))
            mod_files.append([path.name, checksum])
        return original_load(cache_dir, files)

    exporter.gamedb.load_index = load_index
    # Legacy collect_stable повторяет чтение изменяющегося источника;
    # проект проверяет хеши и отказывает при первом изменении.
    exporter.collect_stable = exporter.collect
    snapshot = exporter.build_snapshot(str(save), request["faction_sid"], exporter.Timer())
    if denied:
        raise ExtractionError("Конвертер пытался записать защищённые данные")
    if any(file_hash(path) != checksum for path, checksum in inputs):
        raise ExtractionError("Данные игры изменились во время извлечения")
    used = sorted({Path(m.__file__).resolve().relative_to(converter).as_posix()
                   for m in list(sys.modules.values()) if getattr(m, "__file__", None)
                   and Path(m.__file__).suffix == ".py"
                   and Path(m.__file__).resolve().is_relative_to(converter / "tools")})
    gd = snapshot.get("meta", {}).get("game_data", {})
    mods = {"files": mod_files, "mods": gd.get("mods"), "missing": gd.get("missing"),
            "mods_source": gd.get("mods_source")}
    return {"snapshot": snapshot, "converter_files": used,
            "mods": mods, "mods_sha256": digest(canonical(mods))}


def main() -> int:
    try:
        request = read_json(sys.stdin.buffer.read())
        # Произвольный stdout legacy-кода не смешивается с протоколом.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run(request)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
