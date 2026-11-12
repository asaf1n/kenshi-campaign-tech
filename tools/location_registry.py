"""Реестр локаций кампании: обнаружение из сейва и отметки зачистки, подтверждённые при [итог]."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from tools import gamedb
from tools.io_utils import ExtractionError, canonical, digest, file_hash, tree_hashes
from tools.kenshi_gd import read_file
from tools.normalization import scrub
from tools.save_reader import parse_town_discovery

REGISTRY_FILENAME = "location_registry.json"
PROGRESS_RELATIVE = Path("Campaign/Хроники Хот Лон/locations_progress.json")
REGISTRY_MAX_CHARS = 150_000
DISCOVERY_SOURCE = "quick.save:Town state.discovered"
STATIC_FIELDS = ("instance_id", "name_ru", "name_en", "kind", "region", "x", "z",
                 "is_secret", "is_public", "unexplored_name")
RELEASE_FIELDS = ("campaign_id", "snapshot_id", "world_ref_id", "game_time")
REGISTRY_FORMAT = "kenshi-location-registry/3"
PROGRESS_FORMAT = "kenshi-location-progress/3"
LEGACY_PROGRESS_FORMATS = ("kenshi-location-progress/1", "kenshi-location-progress/2")
MARK_FIELDS = ("evidence", "game_time")
GAME_TIME = re.compile(r"Day ([0-9]+) ([0-9]|[01][0-9]|2[0-3]):([0-5][0-9])")


def _game_minutes(value: Any) -> int | None:
    match = GAME_TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    day, hour, minute = map(int, match.groups())
    return day * 1440 + hour * 60 + minute


def read_discovery_save(save_dir: Path, expected_sha256: str) -> dict[str, bool]:
    """Восстанавливает признак для неизменяемого снимка по точному исходному сейву."""
    quick = save_dir / "quick.save"
    if not quick.is_file() or digest(canonical(tree_hashes(save_dir))) != expected_sha256:
        raise ExtractionError("Каталог сейва не совпадает с source_sha256 снимка")
    try:
        records = read_file(quick.read_bytes())["records"]
    except (KeyError, OSError, ValueError) as error:
        raise ExtractionError("Не удалось прочитать Town state из quick.save") from error
    if digest(canonical(tree_hashes(save_dir))) != expected_sha256:
        raise ExtractionError("Сейв изменился во время чтения Town state")
    return parse_town_discovery(records)


def resolve_template_factions(world_ref: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """Читает TOWN.refs.faction только из файлов, которыми построен справочник мира."""
    hashes = world_ref.get("source_hashes")
    mods = world_ref.get("mods")
    if not hashes or not mods:
        return {}
    game = Path(os.environ.get("KENSHI_DIR", gamedb.KENSHI)).resolve()
    workshop = Path(os.environ.get("KENSHI_WORKSHOP_DIR", gamedb.WORKSHOP)).resolve()
    active = gamedb.active_files(mods, game, workshop)
    if active["missing"] or not active["files"]:
        return {}

    sources = {}
    for name in active["files"]:
        path = Path(name).resolve()
        if path.is_relative_to(game):
            key = path.relative_to(game).as_posix()
        elif path.is_relative_to(workshop):
            key = "workshop/" + path.relative_to(workshop).as_posix()
        else:
            key = path.name
        if key not in hashes:
            return {}
        sources[path] = hashes[key]
    try:
        if any(file_hash(path) != expected for path, expected in sources.items()):
            return {}
        records = gamedb.build(list(sources))["records"]
        if any(file_hash(path) != expected for path, expected in sources.items()):
            return {}
    except (OSError, ValueError):
        return {}

    ru, _, by_msgid = gamedb.load_po()
    result = {}
    for sid in {row["template_sid"] for row in world_ref["locations"]}:
        town = records.get(sid, {})
        refs = town.get("refs", {}).get("faction", {})
        if town.get("type_id") != 13 or gamedb._removed(town) or len(refs) != 1:
            continue
        faction_sid = next(iter(refs))
        faction = records.get(faction_sid, {})
        if faction.get("type_id") == 10 and faction.get("name") and not gamedb._removed(faction):
            result[sid] = {"sid": faction_sid, "name_en": faction["name"],
                           "name_ru": ru.get(faction_sid) or by_msgid.get(faction["name"])}
    return result


def build_registry(
    manifest: dict[str, Any],
    world_ref: dict[str, Any],
    *,
    discovery: dict[str, bool] | None = None,
    factions: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Реестр выпуска: статика справочника и признак обнаружения из сейва; отметки зачистки добавляет доставка."""
    regions = {region["name_en"]: region["name_ru"] for region in world_ref.get("regions", [])
               if region.get("name_ru")}
    rows = {}
    for source in world_ref["locations"]:
        sid = source["instance_id"]
        if sid in rows:
            raise ValueError(f"Повтор instance_id в справочнике: {sid}")
        row = {key: source.get(key) for key in STATIC_FIELDS}
        row["region"] = regions.get(row["region"], row["region"])
        if discovery and sid in discovery:
            row.update(discovered=discovery[sid], discovered_source=DISCOVERY_SOURCE)
        faction = (factions or {}).get(source["template_sid"])
        if faction:
            row["template_faction"] = dict(faction)
        rows[sid] = row
    return {
        "format": REGISTRY_FORMAT,
        **{field: manifest.get(field) for field in RELEASE_FIELDS},
        "locations": [rows[sid] for sid in sorted(rows)],
    }


def upgrade_progress(progress: dict[str, Any]) -> dict[str, Any]:
    """kenshi-location-progress/1, /2 -> /3: отметкой остаётся только зачистка (прежний статус explored).

    Отметки «посещено» снимаются: известность места даёт признак обнаружения из сейва.
    При повторе instance_id остаётся первая отметка explored. Иные форматы возвращаются без изменений.
    """
    if not isinstance(progress, dict) or progress.get("format") not in LEGACY_PROGRESS_FORMATS:
        return progress
    rows: dict[str, dict[str, Any]] = {}
    for row in progress.get("locations") or []:
        if not isinstance(row, dict):
            raise ValueError("Запись ручных отметок не является объектом")
        if row.get("status") == "explored" and row.get("instance_id") not in rows:
            rows[row["instance_id"]] = {"instance_id": row["instance_id"],
                                        **{key: row[key] for key in MARK_FIELDS if key in row}}
    return {**progress, "format": PROGRESS_FORMAT, "locations": list(rows.values())}


def _progress_rows(progress: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in progress["locations"]:
        key = row["instance_id"]
        if key in result:
            raise ValueError(f"Повтор отметки локации {key}")
        result[key] = row
    return result


def validate_progress(
    progress: dict[str, Any],
    campaign_id: str | None,
    world_ref: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> list[str]:
    """Проверяет отметки зачистки, возвращает ID сохранённых несопоставленных отметок."""
    from tools.validation import validate

    progress = upgrade_progress(progress)
    validate(progress, "location-progress")
    if progress["campaign_id"] != campaign_id:
        raise ValueError("Ручные отметки относятся к другой кампании")
    if scrub(progress) != progress:
        raise ValueError("Ручные отметки содержат локальный путь или данные доступа")
    entries = _progress_rows(progress)
    baseline = {}
    if previous is not None:
        previous = upgrade_progress(previous)
        validate(previous, "location-progress")
        if previous["campaign_id"] != campaign_id:
            raise ValueError("Предыдущие ручные отметки относятся к другой кампании")
        baseline = _progress_rows(previous)
        for key in baseline:
            if key not in entries:
                raise ValueError(f"Отметка зачистки {key} удалена")

    world_ids = {row["instance_id"] for row in world_ref["locations"]}
    mismatched_world = progress["world_ref_id"] != world_ref["world_ref_id"]
    unmatched = set()
    for instance, entry in entries.items():
        if instance not in world_ids:
            if not mismatched_world or baseline.get(instance) != entry:
                raise ValueError(f"Неизвестный instance_id ручной отметки: {instance}")
            unmatched.add(instance)
    return sorted(unmatched)


def _applies(mark: dict[str, Any], release: dict[str, Any]) -> bool:
    mark_time = _game_minutes(mark.get("game_time"))
    release_time = _game_minutes(release.get("game_time"))
    return mark_time is None or release_time is None or mark_time <= release_time


def merge_progress(registry: dict[str, Any], progress: dict[str, Any] | None) -> dict[str, Any]:
    """Переносит в реестр отметки зачистки кампании, не позднее времени выпуска."""
    applicable = {}
    progress = upgrade_progress(progress) if progress else progress
    if progress and progress["campaign_id"] == registry["campaign_id"]:
        applicable = {row["instance_id"]: row for row in progress["locations"] if _applies(row, registry)}
    rows = []
    for entry in registry["locations"]:
        row = dict(entry)
        row.pop("cleared", None)
        mark = applicable.get(row["instance_id"])
        if mark is not None:
            row["cleared"] = {key: mark[key] for key in MARK_FIELDS if key in mark}
        rows.append(row)
    return {**registry, "locations": rows}


def validate_registry(registry: dict[str, Any], manifest: dict[str, Any], world_ref: dict[str, Any]) -> None:
    """Проверяет схему, принадлежность выпуску, полноту по справочнику и время отметок зачистки."""
    from tools.validation import validate

    validate(registry, "location-registry")
    registry_bytes(registry)
    if any(registry[key] != manifest.get(key) for key in RELEASE_FIELDS) or registry["world_ref_id"] != world_ref["world_ref_id"]:
        raise ValueError("Реестр локаций относится к другому выпуску")
    world = {row["instance_id"]: row for row in world_ref["locations"]}
    regions = {region["name_en"]: region["name_ru"] for region in world_ref.get("regions", [])
               if region.get("name_ru")}
    seen = set()
    for row in registry["locations"]:
        sid = row["instance_id"]
        if sid in seen or sid not in world:
            raise ValueError(f"Неизвестный или повторный instance_id реестра: {sid}")
        seen.add(sid)
        expected = {key: world[sid].get(key) for key in STATIC_FIELDS}
        expected["region"] = regions.get(expected["region"], expected["region"])
        if any(row[key] != expected[key] for key in STATIC_FIELDS):
            raise ValueError(f"Статические поля реестра расходятся со справочником: {sid}")
        if "cleared" in row and not _applies(row["cleared"], registry):
            raise ValueError(f"Отметка зачистки позже времени выпуска: {sid}")
    if seen != set(world):
        raise ValueError("Реестр покрывает не все локации справочника")


def registry_bytes(registry: dict[str, Any]) -> bytes:
    payload = canonical(registry)
    if len(payload.decode("utf-8")) > REGISTRY_MAX_CHARS:
        raise ValueError(f"Реестр локаций превышает лимит {REGISTRY_MAX_CHARS} символов")
    return payload
