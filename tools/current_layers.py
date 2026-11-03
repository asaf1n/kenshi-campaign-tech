"""Публикационные слои снимка: очищенная проекция current.json и шарды по вопросам LLM.

Исходные снимки в data/snapshots/ и расчёт разниц сохраняют полный набор полей; очистка выполняется
только при формировании публичных файлов. Монолит помещается в published/extended/ (не для сплошного
чтения), а в published/current/ лежат небольшие шарды: по бойцу, по поселению и обзорные файлы мира.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tools.io_utils import canonical, read_json

INDEX_FORMAT = "kenshi-current-index/1"
INDEX_PATH = "current/index.json"
MONOLITH_PATH = "extended/current.json"
# Верхний предел текстовых файлов стартового набора (manifest, brief, squad, changes), символов.
START_SET_LIMIT_CHARS = 140_000
DROPPED_KEYS = frozenset({"raw", "hidden_stats_raw"})
WORLD_DROPPED_KEYS = frozenset({"regions_map", "source_hashes"})
OVERVIEW_KEYS = ("world", "world_state", "diplomacy", "research", "supplies", "squads", "storage_by_location")


def public_projection(value: Any) -> Any:
    """Копия данных без `raw`, `hidden_stats_raw` и навыков со значением строго 1.0 (отсутствующий навык = 1.0)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in DROPPED_KEYS:
                continue
            projected = public_projection(item)
            if key == "skills_base" and isinstance(projected, dict):
                projected = {name: level for name, level in projected.items() if isinstance(level, bool) or level != 1.0}
            out[key] = projected
        return out
    if isinstance(value, list):
        return [public_projection(item) for item in value]
    return value


SAFE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")
RESERVED_NAME = re.compile(r"(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?", re.IGNORECASE)


def _shard_name(identifier: Any) -> str:
    """Имя файла из идентификатора; небезопасные для Windows и GitHub идентификаторы заменяются хешем."""
    text = str(identifier)
    if SAFE_NAME.fullmatch(text) and not text.endswith(".") and not RESERVED_NAME.fullmatch(text):
        return text.replace(":", "_")
    return "x_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def public_world_reference(world_ref: dict[str, Any]) -> dict[str, Any]:
    """Публикуемый справочник мира без растра регионов и контрольных сумм исходников (полный файл остаётся в data/world/)."""
    return {key: value for key, value in world_ref.items() if key not in WORLD_DROPPED_KEYS}


def character_shard_path(character_id: str) -> str:
    """`ps:-843917568` -> `current/characters/ps_-843917568.json`."""
    return f"current/characters/{_shard_name(character_id)}.json"


def building_shard_path(building: dict[str, Any]) -> str:
    """Шард постройки: идентификатор постройки; при его отсутствии берётся хеш записи."""
    ident = building.get("id") or hashlib.sha256(canonical(building)).hexdigest()[:12]
    return f"current/bases/{_shard_name(ident)}.json"


def _put(files: dict[str, bytes], path: str, data: bytes) -> None:
    if any(known.lower() == path.lower() for known in files):
        raise ValueError(f"Повтор имени шарда {path}: идентификаторы записей не уникальны")
    files[path] = data


def build_layers(projected: dict[str, Any]) -> dict[str, bytes]:
    """Шарды и индекс по очищенной проекции: путь в пакете -> канонические байты."""
    files: dict[str, bytes] = {}
    characters = []
    for character in projected.get("characters", []):
        path = character_shard_path(character["id"])
        data = canonical(character)
        _put(files, path, data)
        characters.append({
            "id": character["id"], "name": character.get("name"), "squad_name": character.get("squad_name"),
            "path": path, "size_bytes": len(data),
        })

    prop = projected.get("property", {})
    bases = []
    for building in prop.get("buildings", []):
        path = building_shard_path(building)
        data = canonical(building)
        _put(files, path, data)
        bases.append({
            "town": str(building.get("town") or building.get("zone") or "—"), "id": building.get("id"),
            "name": building.get("name"), "path": path, "size_bytes": len(data),
        })
    bases.sort(key=lambda entry: (entry["town"], str(entry["id"])))
    projected = {**projected, "storage_by_location": prop.get("storage_by_location", {})}

    overview = []
    for key in OVERVIEW_KEYS:
        if key in projected:
            path = f"current/{key}.json"
            data = canonical(projected[key])
            files[path] = data
            overview.append({"name": key, "path": path, "size_bytes": len(data)})

    files[INDEX_PATH] = canonical({
        "format": INDEX_FORMAT,
        "characters": characters,
        "bases": bases,
        "overview": overview,
    })
    return files


def validate_layers(package_dir: Path, artifacts: dict[str, str], current: dict[str, Any], error: type[Exception]) -> None:
    """Шарды и индекс идентичны монолиту (воспроизводятся из него побайтово); стартовый набор укладывается в лимит."""
    index_path = package_dir / artifacts["current_index"]
    if artifacts["current_index"] != INDEX_PATH or not index_path.is_file():
        raise error(f"Индекс шардов отсутствует или расположен не в {INDEX_PATH}")
    if read_json(index_path.read_bytes()).get("format") != INDEX_FORMAT:
        raise error(f"Некорректный формат {INDEX_PATH}")

    expected = build_layers(public_projection(current))
    for rel_path, data in expected.items():
        shard = package_dir / rel_path
        if not shard.is_file() or shard.read_bytes() != data:
            raise error(f"Шард {rel_path} отсутствует или не совпадает с extended/current.json")
    extra = sorted(
        path.relative_to(package_dir).as_posix()
        for path in (package_dir / "current").rglob("*")
        if path.is_file() and path.relative_to(package_dir).as_posix() not in expected
    )
    if extra:
        raise error(f"В current/ есть файлы вне индекса шардов: {extra[:3]}")

    total = 0
    for name in ("manifest.json", artifacts.get("brief"), artifacts.get("squad"), artifacts.get("changes")):
        path = package_dir / name if name else None
        if path is not None and path.is_file():
            total += len(path.read_text(encoding="utf-8"))
    if total > START_SET_LIMIT_CHARS:
        raise error(f"Стартовый набор (manifest, brief, squad, changes) превышает лимит: {total} > {START_SET_LIMIT_CHARS} символов")
