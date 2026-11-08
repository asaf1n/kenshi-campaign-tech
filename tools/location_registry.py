"""Факты посещения, ручные отметки и итоговый реестр локаций кампании."""
from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import gamedb
from tools.chain_timeline import chain_ids
from tools.io_utils import ExtractionError, canonical, digest, file_hash, tree_hashes
from tools.kenshi_gd import read_file
from tools.normalization import scrub
from tools.save_reader import parse_town_discovery

REGISTRY_FILENAME = "location_registry.json"
PROGRESS_RELATIVE = Path("Campaign/Хроники Хот Лон/locations_progress.json")
REGISTRY_MAX_CHARS = 150_000
STATUS_RANK = {"no_evidence": 0, "visited": 1, "explored": 2}
DISCOVERY_SOURCE = "quick.save:Town state.discovered"
STATIC_FIELDS = ("instance_id", "name_ru", "name_en", "kind", "region", "x", "z",
                 "is_secret", "is_public", "unexplored_name")
RELEASE_FIELDS = ("campaign_id", "branch_id", "snapshot_id", "world_ref_id", "game_time")
GAME_TIME = re.compile(r"Day ([0-9]+) ([0-9]|[01][0-9]|2[0-3]):([0-5][0-9])")


def _game_minutes(value: Any) -> int | None:
    match = GAME_TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    day, hour, minute = map(int, match.groups())
    return day * 1440 + hour * 60 + minute


def _format_game_time(minutes: int) -> str:
    day, daytime = divmod(minutes, 1440)
    hour, minute = divmod(daytime, 60)
    return f"Day {day} {hour:02d}:{minute:02d}"


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


def resolve_template_factions(world_ref: dict[str, Any]) -> dict[str, dict[str, str]]:
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

    result = {}
    for sid in {row["template_sid"] for row in world_ref["locations"]}:
        town = records.get(sid, {})
        refs = town.get("refs", {}).get("faction", {})
        if town.get("type_id") != 13 or gamedb._removed(town) or len(refs) != 1:
            continue
        faction_sid = next(iter(refs))
        faction = records.get(faction_sid, {})
        if faction.get("type_id") == 10 and faction.get("name") and not gamedb._removed(faction):
            result[sid] = {"sid": faction_sid, "name_en": faction["name"]}
    return result


def _history(
    manifest: dict[str, Any], index_data: dict[str, Any] | None,
) -> tuple[list[tuple[str, str | None]], bool]:
    """Цепочка по родителям с исключением снимков, отменённых регрессией времени."""
    index = index_data or {}
    snapshots = index.get("snapshots", {})
    snapshot_id = manifest["snapshot_id"]
    campaign_id = manifest.get("campaign_id")
    chain = chain_ids(index, snapshot_id, window=len(snapshots))
    if snapshots.get(chain[0], {}).get("parent_snapshot_id") is not None:
        raise ValueError("Цепочка снимков в индексе оборвана или содержит цикл")
    incomplete = snapshot_id not in snapshots and manifest.get("parent_snapshot_id") is not None
    selected = []
    cutoff = None
    for sid in reversed(chain):
        entry = snapshots.get(sid, {})
        if sid != snapshot_id and entry.get("campaign_id") not in (None, campaign_id):
            break
        game_time = entry.get("game_time") or {}
        minutes = _game_minutes(f"Day {game_time.get('day')} {game_time.get('time')}")
        if sid == snapshot_id:
            release_minutes = _game_minutes(manifest.get("game_time"))
            if release_minutes is not None:
                minutes = release_minutes
        if minutes is not None:
            if cutoff is not None and minutes > cutoff:
                continue
            cutoff = minutes
        selected.append((sid, _format_game_time(minutes) if minutes is not None else None))
    return list(reversed(selected)), incomplete


def _counts(locations: dict[str, Any], group: str, world_ids: set[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in locations[group]:
        if row.get("resolution_status") == "in_location":
            sid = row["location"]["instance_id"]
            if sid in world_ids:
                counts[sid] += 1
    return counts


def build_registry(
    manifest: dict[str, Any],
    world_ref: dict[str, Any],
    locations: dict[str, Any],
    index_data: dict[str, Any] | None,
    load_locations: Callable[[str], dict[str, Any] | None],
    *,
    discovery: dict[str, bool] | None = None,
    factions: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Формирует технический реестр с автостатусом и покрытием цепочки."""
    rows = {}
    for source in world_ref["locations"]:
        sid = source["instance_id"]
        if sid in rows:
            raise ValueError(f"Повтор instance_id в справочнике: {sid}")
        row = {key: source.get(key) for key in STATIC_FIELDS}
        row.update(auto_status="no_evidence", status="no_evidence", status_source="none")
        if discovery and sid in discovery:
            row.update(discovered=discovery[sid], discovered_source=DISCOVERY_SOURCE)
        faction = (factions or {}).get(source["template_sid"])
        if faction:
            row["template_faction"] = dict(faction)
        rows[sid] = row

    history, chain_incomplete = _history(manifest, index_data)
    world_ids = set(rows)
    resolved = 0
    for sid, game_time in history:
        report = locations if sid == manifest["snapshot_id"] else load_locations(sid)
        if report is None:
            continue
        if report.get("snapshot_id") != sid:
            raise ValueError(f"Файл разрешения локаций относится к другому снимку: {sid}")
        resolved += 1
        for instance, count in _counts(report, "characters", world_ids).items():
            point = {"snapshot_id": sid, "game_time": game_time, "count": count}
            rows[instance].setdefault("presence", {"first": point})["last"] = point
        for instance, count in _counts(report, "buildings", world_ids).items():
            rows[instance]["property"] = {"snapshot_id": sid, "game_time": game_time, "count": count}
    for row in rows.values():
        if "presence" in row or "property" in row:
            row.update(auto_status="visited", status="visited", status_source="auto")
    coverage = {"chain_snapshots": len(history), "location_snapshots": resolved}
    if chain_incomplete:
        coverage["chain_incomplete"] = True
    return {
        "format": "kenshi-location-registry/1",
        **{field: manifest.get(field) for field in RELEASE_FIELDS},
        "coverage": coverage,
        "locations": [rows[sid] for sid in sorted(rows)],
    }


def _progress_rows(progress: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for row in progress["locations"]:
        key = (row["branch_id"], row["instance_id"])
        if key in result:
            raise ValueError(f"Повтор отметки локации {key[1]} для ветки {key[0]}")
        result[key] = row
    return result


def validate_progress(
    progress: dict[str, Any],
    campaign_id: str | None,
    world_ref: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> list[str]:
    """Проверяет ручной файл, возвращает ID сохранённых несопоставленных отметок."""
    from tools.validation import validate

    validate(progress, "location-progress")
    if progress["campaign_id"] != campaign_id:
        raise ValueError("Ручные отметки относятся к другой кампании")
    if scrub(progress) != progress:
        raise ValueError("Ручные отметки содержат локальный путь или данные доступа")
    entries = _progress_rows(progress)
    baseline = {}
    if previous is not None:
        validate(previous, "location-progress")
        if previous["campaign_id"] != campaign_id:
            raise ValueError("Предыдущие ручные отметки относятся к другой кампании")
        baseline = _progress_rows(previous)
        for key, prior in baseline.items():
            if key not in entries or STATUS_RANK[entries[key]["status"]] < STATUS_RANK[prior["status"]]:
                raise ValueError(f"Отметка локации {key[1]} для ветки {key[0]} удалена или понижена")

    world_ids = {row["instance_id"] for row in world_ref["locations"]}
    mismatched_world = progress["world_ref_id"] != world_ref["world_ref_id"]
    unmatched = set()
    for key, entry in entries.items():
        instance = key[1]
        if instance not in world_ids:
            if not mismatched_world or baseline.get(key) != entry:
                raise ValueError(f"Неизвестный instance_id ручной отметки: {instance}")
            unmatched.add(instance)
    return sorted(unmatched)


def _applies(manual: dict[str, Any], release: dict[str, Any]) -> bool:
    mark_time = _game_minutes(manual.get("game_time"))
    release_time = _game_minutes(release.get("game_time"))
    return manual["branch_id"] == release["branch_id"] and (
        mark_time is None or release_time is None or mark_time <= release_time
    )


def _status(auto: str, manual: dict[str, Any] | None) -> tuple[str, str]:
    if manual is not None and STATUS_RANK[manual["status"]] > STATUS_RANK[auto]:
        return manual["status"], "manual"
    return auto, "auto" if auto == "visited" else "none"


def merge_progress(registry: dict[str, Any], progress: dict[str, Any] | None) -> dict[str, Any]:
    """Пересчитывает итог от автостатуса и проверенных отметок текущей ветки."""
    applicable = {}
    if progress and progress["campaign_id"] == registry["campaign_id"]:
        applicable = {row["instance_id"]: row for row in progress["locations"] if _applies(row, registry)}
    rows = []
    for entry in registry["locations"]:
        row = dict(entry)
        mark = applicable.get(row["instance_id"])
        row.pop("manual", None)
        if mark is not None:
            row["manual"] = {key: value for key, value in mark.items() if key != "instance_id"}
        row["status"], row["status_source"] = _status(row["auto_status"], mark)
        rows.append(row)
    return {**registry, "locations": rows}


def _validate_evidence(
    row: dict[str, Any],
    release_minutes: int | None,
    chain: dict[str, tuple[int, str | None]] | None,
) -> None:
    sid = row["instance_id"]
    presence = row.get("presence")
    points = [presence["first"], presence["last"]] if presence else []
    if "property" in row:
        points.append(row["property"])
    for point in points:
        minutes = _game_minutes(point["game_time"])
        if minutes is not None and release_minutes is not None and minutes > release_minutes:
            raise ValueError(f"Свидетельство реестра позже выпуска: {sid}")
        if chain is not None:
            snapshot = chain.get(point["snapshot_id"])
            if snapshot is None or snapshot[1] != point["game_time"]:
                raise ValueError(f"Свидетельство реестра вне цепочки выпуска или расходится с её временем: {sid}")
    if presence:
        first, last = presence["first"], presence["last"]
        first_id, last_id = first["snapshot_id"], last["snapshot_id"]
        first_time, last_time = _game_minutes(first["game_time"]), _game_minutes(last["game_time"])
        if (first_id == last_id and first != last
                or chain is not None and chain[first_id][0] > chain[last_id][0]
                or first_time is not None and last_time is not None and first_time > last_time):
            raise ValueError(f"Первая и последняя фиксации присутствия противоречат друг другу: {sid}")


def validate_registry(
    registry: dict[str, Any],
    manifest: dict[str, Any],
    world_ref: dict[str, Any],
    *,
    index_data: dict[str, Any] | None = None,
) -> None:
    """Проверяет схему, принадлежность выпуску, покрытие и вычислимость итогов."""
    from tools.validation import validate

    validate(registry, "location-registry")
    registry_bytes(registry)
    if any(registry[key] != manifest.get(key) for key in RELEASE_FIELDS) or registry["world_ref_id"] != world_ref["world_ref_id"]:
        raise ValueError("Реестр локаций относится к другому выпуску")
    coverage = registry["coverage"]
    if coverage["location_snapshots"] > coverage["chain_snapshots"]:
        raise ValueError("Покрытие реестра превышает длину цепочки снимков")
    release_minutes = _game_minutes(registry["game_time"])
    snapshots = (index_data or {}).get("snapshots", {})
    chain = None
    if registry["snapshot_id"] in snapshots:
        history, _ = _history(manifest, index_data)
        if coverage.get("chain_incomplete") or coverage["chain_snapshots"] != len(history):
            raise ValueError("Покрытие реестра расходится с цепочкой снимков в индексе")
        chain = {snapshot_id: (order, time) for order, (snapshot_id, time) in enumerate(history)}
    elif coverage.get("chain_incomplete"):
        if manifest.get("parent_snapshot_id") is None:
            raise ValueError("Неполная цепочка объявлена у снимка без родителя")
        game_time = _format_game_time(release_minutes) if release_minutes is not None else None
        chain = {registry["snapshot_id"]: (0, game_time)}
    world = {row["instance_id"]: row for row in world_ref["locations"]}
    seen = set()
    for row in registry["locations"]:
        sid = row["instance_id"]
        if sid in seen or sid not in world:
            raise ValueError(f"Неизвестный или повторный instance_id реестра: {sid}")
        seen.add(sid)
        if any(row[key] != world[sid].get(key) for key in STATIC_FIELDS):
            raise ValueError(f"Статические поля реестра расходятся со справочником: {sid}")
        _validate_evidence(row, release_minutes, chain)
        auto = "visited" if "presence" in row or "property" in row else "no_evidence"
        manual = row.get("manual")
        if manual is not None and not _applies(manual, registry):
            raise ValueError(f"Ручная отметка не относится к ветке или времени выпуска: {sid}")
        if row["auto_status"] != auto or (row["status"], row["status_source"]) != _status(auto, manual):
            raise ValueError(f"Статус реестра расходится со свидетельствами: {sid}")
    if seen != set(world):
        raise ValueError("Реестр покрывает не все локации справочника")


def registry_bytes(registry: dict[str, Any]) -> bytes:
    payload = canonical(registry)
    if len(payload.decode("utf-8")) > REGISTRY_MAX_CHARS:
        raise ValueError(f"Реестр локаций превышает лимит {REGISTRY_MAX_CHARS} символов")
    return payload
