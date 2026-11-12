"""Проверка проектных схем Draft 2020-12 и валидация пакета публикации."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from tools.io_utils import linked, read_json
from tools.location_registry import REGISTRY_MAX_CHARS, REGISTRY_FILENAME, validate_registry
from tools.normalization import _AUTH, _PATH, _SECRET_TEXT, _TOKEN

ROOT = Path(__file__).resolve().parents[1]


class PublicationValidationError(ValueError):
    """Отказ валидации пакета публикации."""


class WorldReferenceValidationError(ValueError):
    """Отказ валидации справочника мира."""


class LocationsValidationError(ValueError):
    """Отказ валидации пространственного разрешения локаций."""


class IndexValidationError(ValueError):
    """Отказ валидации реестра снимков."""


class TimelineValidationError(ValueError):
    """Отказ валидации события или журнала событий."""


_TIMELINE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def validate_timeline_id(value: str, field: str = "ID") -> None:
    """Идентификатор не должен менять смысл при использовании как имени файла."""
    if (
        not isinstance(value, str)
        or not _TIMELINE_ID.fullmatch(value)
        or ".." in value
        or value.endswith(".")
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise TimelineValidationError(f"Небезопасный идентификатор {field}: {value!r}")


def _validate_evidence_ref(ref: str) -> None:
    parts = ref.split("/")
    if (
        any(char in ref for char in '\\:<>"|?*')
        or any(ord(char) < 32 or ord(char) == 127 for char in ref)
        or any(
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
            for part in parts
        )
    ):
        raise TimelineValidationError(f"Небезопасная относительная ссылка evidence.ref: {ref!r}")


def validate(value: Any, schema_name: str) -> None:
    if schema_name not in {"manifest", "timeline-event", "world-reference", "locations", "location-registry",
                           "location-progress", "entities-progress", "unique-entities-registry", "index", "session",
                           "screenshots-index", "screenshot-annotations"}:
        raise ValueError(f"Неизвестная проектная схема: {schema_name}")
    schema = read_json((ROOT / "contracts" / f"{schema_name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def validate_manifest(manifest: dict[str, Any]) -> None:
    validate(manifest, "manifest")


def validate_unique_entities_registry(payload: dict[str, Any]) -> None:
    validate(payload, "unique-entities-registry")


def validate_entities_progress(data: dict[str, Any]) -> dict[str, Any]:
    """Проверить ручные свидетельства исходов по контракту кампании."""
    validate(data, "entities-progress")
    return data


def load_entities_progress(raw: bytes | str) -> dict[str, Any]:
    """Загрузить ручной слой без повторяющихся ключей и проверить схему."""
    return validate_entities_progress(read_json(raw.encode("utf-8") if isinstance(raw, str) else raw))


def validate_event(event: dict[str, Any], index_data: dict[str, Any] | None = None) -> None:
    """Проверить схему, идентичность и достаточный вид источника события."""
    try:
        validate(event, "timeline-event")
    except ValidationError as error:
        raise TimelineValidationError(f"Ошибка схемы timeline-event: {error.message}") from error

    for field in ("event_id", "session_id", "snapshot_id", "corrects_event_id"):
        if field in event:
            validate_timeline_id(event[field], field)

    snapshot_id = event.get("snapshot_id")
    if snapshot_id == event["session_id"]:
        raise TimelineValidationError("session_id не должен совпадать со snapshot_id")
    if index_data is not None:
        if not isinstance(index_data, dict) or not isinstance(index_data.get("snapshots"), dict):
            raise TimelineValidationError("Индекс событий обязан содержать объект snapshots")
        if snapshot_id is not None and snapshot_id not in index_data["snapshots"]:
            raise TimelineValidationError(f"Событие ссылается на отсутствующий снимок: {snapshot_id}")

    source_kinds = {source["kind"] for source in event["evidence"]}
    for source in event["evidence"]:
        _validate_evidence_ref(source["ref"])
    if event["status"] == "observed" and not source_kinds & {"snapshot", "diff"}:
        raise TimelineValidationError("Событие observed требует evidence вида snapshot или diff")
    if event["status"] == "confirmed" and not source_kinds & {"user_note", "screenshot"}:
        raise TimelineValidationError("Событие confirmed требует evidence вида user_note или screenshot")

    if event["kind"] != "correction" and "corrects_event_id" in event:
        raise TimelineValidationError("corrects_event_id допустим только для kind=correction")
    if event.get("corrects_event_id") == event["event_id"]:
        raise TimelineValidationError(f"Коррекция {event['event_id']} ссылается сама на себя")


def validate_timeline(
    events: list[dict[str, Any]],
    index_data: dict[str, Any] | None = None,
    *,
    projection: bool = False,
) -> dict[str, Any]:
    """Проверить целостность журнала независимо от порядка поступления событий."""
    if not isinstance(events, list):
        raise TimelineValidationError("Журнал событий обязан быть списком")
    by_id: dict[str, dict[str, Any]] = {}
    corrections: dict[str, str] = {}
    corrected_by: dict[str, str] = {}
    for event in events:
        validate_event(event, index_data)
        event_id = event["event_id"]
        if event_id in by_id:
            raise TimelineValidationError(f"Дублирующийся event_id в журнале: {event_id}")
        if projection and event["status"] == "inferred":
            raise TimelineValidationError(f"Гипотеза inferred недопустима в опубликованной проекции: {event_id}")
        by_id[event_id] = event
        target = event.get("corrects_event_id")
        if target is not None:
            if target in corrected_by:
                raise TimelineValidationError(
                    f"Неоднозначные коррекции события {target}: {corrected_by[target]} и {event_id}"
                )
            corrected_by[target] = event_id
            corrections[event_id] = target

    for event_id, target in corrections.items():
        # Проекция содержит только действующее утверждение; оригинал хранится в журнале.
        if target not in by_id and not projection:
            raise TimelineValidationError(f"Коррекция {event_id} ссылается на отсутствующее событие: {target}")

    checked: set[str] = set()
    for event_id in corrections:
        visited: set[str] = set()
        current = event_id
        while current in corrections and current not in checked:
            if current in visited:
                raise TimelineValidationError(f"Обнаружен цикл коррекций события: {event_id}")
            visited.add(current)
            current = corrections[current]
        checked.update(visited)

    return {
        "status": "passed",
        "events_count": len(events),
        "corrections_count": len(corrections),
        "projection": projection,
        "checks": ["schema", "safe_references", "evidence_kinds", "unique_event_ids", "correction_graph"],
    }


def validate_index(index_data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(index_data, dict):
        raise IndexValidationError("Индекс обязан быть объектом")
    try:
        validate(index_data, "index")
    except (ValidationError, Exception) as error:
        raise IndexValidationError(f"Ошибка валидации схемы index: {error}") from error

    snapshots = index_data.get("snapshots", {})
    campaigns = index_data.get("campaigns", {})

    # 1. Проверка соответствия ключей и snapshot_id внутри записей
    for sid_key, snap_entry in snapshots.items():
        if snap_entry.get("snapshot_id") != sid_key:
            raise IndexValidationError(
                f"Несовпадение ключа снимка '{sid_key}' и поля snapshot_id '{snap_entry.get('snapshot_id')}'"
            )

    # 2. Проверка связей родительства (отсутствие сирот, родитель из той же кампании)
    for sid, snap_entry in snapshots.items():
        parent_id = snap_entry.get("parent_snapshot_id")
        if parent_id is not None:
            if parent_id == sid:
                raise IndexValidationError(f"Снимок {sid} ссылается сам на себя как на родителя (цикл)")
            if parent_id not in snapshots:
                raise IndexValidationError(f"Снимок {sid} ссылается на отсутствующего родителя '{parent_id}' (сирота)")
            if snapshots[parent_id].get("campaign_id") != snap_entry.get("campaign_id"):
                raise IndexValidationError(f"Родитель снимка {sid} относится к другой кампании")

    # 3. Защита от циклов (cycle detection)
    for sid in snapshots:
        curr = sid
        visited = set()
        while curr:
            if curr in visited:
                raise IndexValidationError(f"Обнаружен цикл в графе родительства снимка: {sid}")
            visited.add(curr)
            curr = snapshots[curr].get("parent_snapshot_id")

    # 4. Головы кампаний
    for cid, entry in campaigns.items():
        head_id = entry.get("head_snapshot_id")
        if head_id is not None:
            if head_id not in snapshots:
                raise IndexValidationError(f"Голова кампании '{cid}' отсутствует в snapshots: '{head_id}'")
            if snapshots[head_id].get("campaign_id") != cid:
                raise IndexValidationError(
                    f"Голова кампании '{cid}' имеет campaign_id '{snapshots[head_id].get('campaign_id')}'")
    missing = {e.get("campaign_id") for e in snapshots.values()} - set(campaigns)
    if missing:
        raise IndexValidationError(f"Кампании без головы в индексе: {sorted(missing)}")

    return {
        "status": "passed",
        "snapshots_count": len(snapshots),
        "campaigns": {cid: entry.get("head_snapshot_id") for cid, entry in sorted(campaigns.items())},
        "checks": [
            "schema",
            "snapshot_id_keys",
            "no_orphans",
            "no_cycles",
            "campaign_heads",
        ],
    }


def validate_world_reference(world_ref: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(world_ref, dict):
        raise WorldReferenceValidationError("Справочник мира обязан быть объектом")
    try:
        validate(world_ref, "world-reference")
    except (ValidationError, Exception) as error:
        raise WorldReferenceValidationError(f"Ошибка валидации схемы world-reference: {error}") from error

    # 1. Глобальная уникальность instance_id
    seen_ids: set[str] = set()
    locations = world_ref.get("locations", [])
    for loc in locations:
        iid = loc.get("instance_id")
        if iid in seen_ids:
            raise WorldReferenceValidationError(f"Дублирующийся instance_id локации: {iid}")
        seen_ids.add(iid)

    # 2. Однозначность статического индекса зданий (zone, building_id) -> instance_id
    seen_buildings: dict[tuple[str, int], str] = {}
    for loc in locations:
        iid = loc["instance_id"]
        for b in loc.get("static_buildings", []):
            bkey = (b["zone"], b["building_id"])
            if bkey in seen_buildings:
                raise WorldReferenceValidationError(
                    f"Коллизия статического здания (zone={bkey[0]}, building_id={bkey[1]}): "
                    f"привязано к {seen_buildings[bkey]} и {iid}"
                )
            seen_buildings[bkey] = iid

    # 3. Попарное непересечение r_i + r_j <= 0.95 * distance(i, j) для всех пар локаций
    n = len(locations)
    for i in range(n):
        loc_i = locations[i]
        ri = loc_i["confirmed_radius"]
        xi, zi = loc_i["x"], loc_i["z"]
        for j in range(i + 1, n):
            loc_j = locations[j]
            rj = loc_j["confirmed_radius"]
            xj, zj = loc_j["x"], loc_j["z"]
            d = math.hypot(xi - xj, zi - zj)
            if ri + rj > 0.95 * d + 1e-4:
                raise WorldReferenceValidationError(
                    f"Нарушение непересечения радиусов: {loc_i['instance_id']} (r={ri}) и "
                    f"{loc_j['instance_id']} (r={rj}) на расстоянии d={d:.2f} (r_i+r_j={ri+rj:.2f} > {0.95*d:.2f})"
                )

    # 4. Сумма длин RLE-сегментов строго равна 65536 (растр отсутствует в публикуемой проекции)
    if "regions_map" in world_ref:
        total_len = sum(seg["length"] for seg in world_ref["regions_map"].get("rle", []))
        if total_len != 65536:
            raise WorldReferenceValidationError(
                f"Сумма длин RLE-сегментов карты регионов {total_len} != 65536"
            )

    # 5. Целостность ссылок на палитру BIOME_GROUP
    regions = world_ref.get("regions", [])
    region_names = {r["name_en"] for r in regions}
    region_indices = {r["index"] for r in regions}

    for loc in locations:
        reg = loc.get("region")
        if reg is not None and reg not in region_names:
            raise WorldReferenceValidationError(
                f"Локация {loc['instance_id']} ссылается на неизвестный регион: {reg}"
            )

    for seg in world_ref.get("regions_map", {}).get("rle", []):
        idx = seg["index"]
        if idx not in region_indices:
            raise WorldReferenceValidationError(
                f"RLE-сегмент ссылается на неизвестный индекс палитры регионов: {idx}"
            )

    return {
        "status": "passed",
        "world_ref_id": world_ref.get("world_ref_id"),
        "checks": [
            "schema",
            "unique_instance_ids",
            "unique_static_buildings",
            "non_overlapping_radii",
            "rle_length_sum",
            "palette_integrity",
        ],
    }


def validate_locations(
    locations: dict[str, Any],
    world_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Семантическая валидация артефакта kenshi-locations/1."""
    if not isinstance(locations, dict):
        raise LocationsValidationError("Артефакт locations обязан быть объектом")
    try:
        validate(locations, "locations")
    except (ValidationError, Exception) as error:
        raise LocationsValidationError(f"Ошибка валидации схемы locations: {error}") from error

    # 1. Уникальность characters[].id и согласованность статусов
    seen_char_ids: set[str] = set()
    for char in locations.get("characters", []):
        cid = char.get("id")
        if not cid or not str(cid).strip():
            raise LocationsValidationError("Пустой или отсутствующий id персонажа")
        if cid in seen_char_ids:
            raise LocationsValidationError(f"Дубликат id персонажа в locations: '{cid}'")
        seen_char_ids.add(cid)

        res_stat = char.get("resolution_status")
        loc = char.get("location")
        if res_stat == "in_location" and not loc:
            raise LocationsValidationError(f"Персонаж {cid} имеет статус 'in_location', но location равен null")
        if res_stat in ("near_location", "in_region", "unknown") and loc is not None:
            raise LocationsValidationError(f"Персонаж {cid} имеет статус '{res_stat}', но location не равен null")

    # 2. Уникальность составного ключа построек (zone, id) и согласованность статусов
    seen_bldg_keys: set[str] = set()
    for bldg in locations.get("buildings", []):
        bkey = bldg.get("building_key")
        bid = bldg.get("id")
        if not bid or not str(bid).strip():
            raise LocationsValidationError("Пустой или отсутствующий id постройки")
        if not bkey or not str(bkey).strip():
            raise LocationsValidationError("Пустой или отсутствующий building_key постройки")
        if bkey in seen_bldg_keys:
            raise LocationsValidationError(f"Дубликат составного ключа постройки: '{bkey}'")
        seen_bldg_keys.add(bkey)

        res_stat = bldg.get("resolution_status")
        loc = bldg.get("location")
        if res_stat == "in_location" and not loc:
            raise LocationsValidationError(f"Постройка {bkey} имеет статус 'in_location', но location равен null")
        if res_stat in ("near_location", "in_region", "unknown") and loc is not None:
            raise LocationsValidationError(f"Постройка {bkey} имеет статус '{res_stat}', но location не равен null")

    # 3. Согласованность spatial_groups
    group_char_count = sum(len(g.get("characters", [])) for g in locations.get("spatial_groups", []))
    if group_char_count != len(seen_char_ids):
        raise LocationsValidationError(
            f"Несовпадение числа участников в spatial_groups ({group_char_count}) и characters ({len(seen_char_ids)})"
        )

    # 4. Проверка ссылок на world_ref
    if world_ref is not None:
        if locations.get("world_ref_id") != world_ref.get("world_ref_id"):
            raise LocationsValidationError(
                f"Несовпадение world_ref_id: в locations '{locations.get('world_ref_id')}', "
                f"а в world_ref '{world_ref.get('world_ref_id')}'"
            )
        known_instances = {l["instance_id"] for l in world_ref.get("locations", [])}
        for char in locations.get("characters", []):
            if char.get("location"):
                iid = char["location"]["instance_id"]
                if iid not in known_instances:
                    raise LocationsValidationError(f"Персонаж {char['id']} ссылается на неизвестную локацию: '{iid}'")
            for near in char.get("nearby_locations", []):
                niid = near["instance_id"]
                if niid not in known_instances:
                    raise LocationsValidationError(f"Персонаж {char['id']} ссылается на неизвестную окрестность: '{niid}'")

        for bldg in locations.get("buildings", []):
            if bldg.get("location"):
                iid = bldg["location"]["instance_id"]
                if iid not in known_instances:
                    raise LocationsValidationError(f"Постройка {bldg['building_key']} ссылается на неизвестную локацию: '{iid}'")
            for near in bldg.get("nearby_locations", []):
                niid = near["instance_id"]
                if niid not in known_instances:
                    raise LocationsValidationError(f"Постройка {bldg['building_key']} ссылается на неизвестную окрестность: '{niid}'")

    return {
        "status": "passed",
        "snapshot_id": locations.get("snapshot_id"),
        "world_ref_id": locations.get("world_ref_id"),
        "checks": [
            "schema",
            "unique_characters",
            "unique_building_keys",
            "resolution_consistency",
            "spatial_groups_coverage",
        ],
    }


def check_examples() -> int:
    for name in ("location-registry", "location-progress", "entities-progress", "unique-entities-registry"):
        Draft202012Validator.check_schema(read_json((ROOT / "contracts" / f"{name}.schema.json").read_bytes()))
    count = 0
    for path in sorted((ROOT / "examples").glob("*.json")):
        value = read_json(path.read_bytes())
        fmt = value.get("format")
        if fmt == "kenshi-publication/2":
            validate(value, "manifest")
        elif fmt == "kenshi-world-reference/1":
            validate_world_reference(value)
        elif fmt == "kenshi-locations/1":
            validate_locations(value)
        elif fmt in ("kenshi-unique-entities-registry/1", "kenshi-unique-entities-registry/2"):
            validate_unique_entities_registry(value)
        elif fmt == "kenshi-entities-progress/1":
            validate_entities_progress(value)
        elif fmt == "kenshi-index/2":
            validate_index(value)
        elif fmt in {"kenshi-session/10", "kenshi-session/11"}:
            validate(value, "session")
        elif fmt == "kenshi-screenshots-index/1":
            validate(value, "screenshots-index")
        elif fmt == "kenshi-screenshot-annotation/1":
            validate(value, "screenshot-annotations")
        else:
            validate_event(value)
        count += 1
    return count


def check_privacy(raw_text: str, filename: str) -> None:
    path_match = _PATH.search(raw_text)
    if path_match:
        raise PublicationValidationError(
            f"В публикуемом файле {filename} обнаружен локальный путь: {path_match.group(0)}"
        )
    auth_match = _AUTH.search(raw_text)
    if auth_match:
        raise PublicationValidationError(
            f"В публикуемом файле {filename} обнаружена строка авторизации: {auth_match.group(0)}"
        )
    token_match = _TOKEN.search(raw_text)
    if token_match:
        raise PublicationValidationError(
            f"В публикуемом файле {filename} обнаружен токен: {token_match.group(0)}"
        )
    sec_match = _SECRET_TEXT.search(raw_text)
    if sec_match:
        raise PublicationValidationError(
            f"В публикуемом файле {filename} обнаружен секрет: {sec_match.group(0)}"
        )


TIMELINE_EMPTY_WARNING = "artifact:timeline: empty"


def package_warnings(package_dir: Path) -> list[str]:
    """Предупреждения, не блокирующие публикацию: пустой, но заявленный в манифесте `timeline.jsonl`."""
    timeline = package_dir / "timeline.jsonl"
    return [TIMELINE_EMPTY_WARNING] if timeline.is_file() and timeline.stat().st_size == 0 else []


def validate_tables(package_dir: Path, manifest: dict[str, Any]) -> None:
    """tables/index.md: тот же снимок, перечисленные CSV существуют, число строк совпадает, посторонних CSV нет."""
    from tools.tables import INDEX_ARTIFACT, INDEX_ROW_RE, INDEX_SNAPSHOT_RE

    if manifest["artifacts"]["tables"] != INDEX_ARTIFACT:
        raise PublicationValidationError(f"artifacts.tables обязан указывать на {INDEX_ARTIFACT}")
    index_path = package_dir / INDEX_ARTIFACT
    index_text = index_path.read_text(encoding="utf-8")
    snapshot = INDEX_SNAPSHOT_RE.search(index_text)
    if not snapshot or snapshot.group(1) != manifest.get("snapshot_id"):
        raise PublicationValidationError("tables/index.md относится к другому снимку, чем manifest.json")
    listed = dict(INDEX_ROW_RE.findall(index_text))
    if not listed:
        raise PublicationValidationError("tables/index.md не перечисляет ни одной таблицы")
    for name, declared in listed.items():
        path = package_dir / "tables" / name
        if not path.is_file() or linked(path):
            raise PublicationValidationError(f"Таблица {name} из tables/index.md не найдена в пакете")
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
            raise PublicationValidationError(f"Таблица {name} должна быть UTF-8 без BOM с окончаниями строк LF")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
        if not rows:
            raise PublicationValidationError(f"Таблица {name} пуста: нет строки заголовка")
        if any(len(r) != len(rows[0]) for r in rows):
            raise PublicationValidationError(f"Таблица {name}: число ячеек в строке не равно числу колонок")
        if len(rows) - 1 != int(declared):
            raise PublicationValidationError(
                f"Таблица {name}: в файле {len(rows) - 1} строк, в tables/index.md указано {declared}"
            )
        if name == "inventory.csv":
            # Ключ сравнения снимков (character_id, base_sid, level, slot, dup_no) должен быть уникален.
            cols = {c: i for i, c in enumerate(rows[0])}
            keys = [tuple(r[cols[c]] for c in ("character_id", "base_sid", "level", "slot", "dup_no")) for r in rows[1:]]
            if len(set(keys)) != len(keys):
                raise PublicationValidationError("Таблица inventory.csv: ключ (character_id, base_sid, level, slot, dup_no) не уникален")
    extra = sorted(p.name for p in (package_dir / "tables").glob("*.csv") if p.name not in listed)
    if extra:
        raise PublicationValidationError(f"Таблицы не перечислены в tables/index.md: {', '.join(extra)}")


# Верхний предел текстовых файлов стартового набора (manifest, brief, changes.md), символов.
# Полный changes.json в стартовый набор не входит: его объём растёт с промежутком между сейвами.
START_SET_LIMIT_CHARS = 140_000


def validate_start_set_size(package_dir: Path, artifacts: dict[str, str]) -> None:
    """Стартовый набор (manifest, brief, changes.md) укладывается в START_SET_LIMIT_CHARS."""
    total = 0
    for name in ("manifest.json", artifacts.get("brief"), artifacts.get("changes_md")):
        path = package_dir / name if name else None
        if path is not None and path.is_file():
            total += len(path.read_text(encoding="utf-8"))
    if total > START_SET_LIMIT_CHARS:
        raise PublicationValidationError(
            f"Стартовый набор (manifest, brief, changes.md) превышает лимит: {total} > {START_SET_LIMIT_CHARS} символов"
        )


def load_location_registry(package_dir: Path, manifest: dict[str, Any], world_ref: dict[str, Any]) -> dict[str, Any]:
    """Читает объявленный реестр по фиксированному пути и сверяет его с выпуском."""
    path = package_dir / REGISTRY_FILENAME
    if manifest.get("artifacts", {}).get("location_registry") != REGISTRY_FILENAME or linked(path):
        raise PublicationValidationError(f"artifacts.location_registry обязан указывать на обычный {REGISTRY_FILENAME}")
    try:
        raw = path.read_bytes()
        if len(raw.decode("utf-8")) > REGISTRY_MAX_CHARS:
            raise ValueError(f"размер превышает {REGISTRY_MAX_CHARS} символов")
        registry = read_json(raw)
        validate_registry(registry, manifest, world_ref)
    except (OSError, ValueError, ValidationError) as error:
        raise PublicationValidationError(f"Ошибка валидации {REGISTRY_FILENAME}: {error}") from error
    return registry


def validate_publication(package_dir: Path, index_data: dict[str, Any] | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file() or linked(manifest_path):
        raise PublicationValidationError("В пакете отсутствует обязательный manifest.json")

    try:
        manifest = read_json(manifest_path.read_bytes())
        validate_manifest(manifest)
    except (ValidationError, Exception) as error:
        raise PublicationValidationError(f"Ошибка валидации схемы manifest.json: {error}") from error

    if index_data is None:
        idx_candidate = package_dir.parent / "data/index.json"
        if idx_candidate.is_file():
            try:
                index_data = read_json(idx_candidate.read_bytes())
            except (OSError, ValueError) as error:
                raise PublicationValidationError("Индекс снимков выпуска не прочитан") from error

    canonical_pub_dir = (ROOT / "published").resolve()
    if package_dir == canonical_pub_dir:
        if manifest.get("campaign_id") != "hot-lon":
            raise PublicationValidationError(
                f"Канонический published/ обязан принадлежать campaign_id='hot-lon', "
                f"получено: '{manifest.get('campaign_id')}'"
            )

    # 1. Проверка артефактов
    artifacts = manifest.get("artifacts", {})
    required_artifacts = {"current", "changes", "brief"}
    if not required_artifacts.issubset(artifacts.keys()):
        missing = required_artifacts - set(artifacts.keys())
        raise PublicationValidationError(f"В artifacts отсутствуют обязательные файлы: {missing}")
    timeline_path = package_dir / "timeline.jsonl"
    if "timeline" not in artifacts and (timeline_path.exists() or linked(timeline_path)):
        raise PublicationValidationError("timeline.jsonl не зарегистрирован в artifacts.timeline")
    if "timeline" in artifacts and artifacts["timeline"] != "timeline.jsonl":
        raise PublicationValidationError("artifacts.timeline обязан указывать на timeline.jsonl")
    registry_path = package_dir / REGISTRY_FILENAME
    if "location_registry" not in artifacts and (registry_path.exists() or linked(registry_path)):
        raise PublicationValidationError(f"{REGISTRY_FILENAME} не зарегистрирован в artifacts.location_registry")
    if "location_registry" in artifacts and "world_reference" not in artifacts:
        raise PublicationValidationError("Для location_registry требуется world_reference выпуска")

    checked_files: set[Path] = {manifest_path}

    for art_key, rel_path in artifacts.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            raise PublicationValidationError(f"Некорректный путь артефакта {art_key}: {rel_path}")
        if (
            rel_path.startswith("/")
            or rel_path.startswith("\\")
            or ":" in rel_path
            or ".." in Path(rel_path).parts
        ):
            raise PublicationValidationError(f"Путь артефакта выходит за пределы пакета: {rel_path}")

        target_path = (package_dir / rel_path).resolve()
        if not target_path.is_relative_to(package_dir):
            raise PublicationValidationError(f"Артефакт {art_key} разрешается вне пакета: {rel_path}")
        if linked(target_path):
            raise PublicationValidationError(f"Артефакт {art_key} является ссылкой (symlink/junction): {rel_path}")
        if not target_path.is_file():
            raise PublicationValidationError(f"Артефакт {art_key} не найден на диске: {rel_path}")

        checked_files.add(target_path)

    if "tables" in artifacts:
        validate_tables(package_dir, manifest)

    if "unique_entities_registry" in artifacts:
        registry_name = artifacts["unique_entities_registry"]
        try:
            registry = read_json((package_dir / registry_name).read_bytes())
            validate_unique_entities_registry(registry)
        except (OSError, ValueError, ValidationError) as error:
            raise PublicationValidationError(f"Ошибка валидации {registry_name}: {error}") from error
        for field in ("campaign_id", "snapshot_id", "world_ref_id", "game_time"):
            if registry.get(field) != manifest.get(field):
                raise PublicationValidationError(f"Несовпадение {field} между manifest и {registry_name}")

    # 2. Семантические проверки current и changes
    current_path = package_dir / artifacts["current"]
    changes_path = package_dir / artifacts["changes"]
    current = read_json(current_path.read_bytes())
    changes = read_json(changes_path.read_bytes())

    if current.get("format") not in {"kenshi-session/10", "kenshi-session/11"}:
        raise PublicationValidationError(f"Некорректный формат current.json: {current.get('format')}")
    if changes.get("format") != "kenshi-session-diff/5":
        raise PublicationValidationError(f"Некорректный формат changes.json: {changes.get('format')}")

    validate_start_set_size(package_dir, artifacts)

    to_ref = changes.get("to") or {}
    if to_ref.get("snapshot_id") != manifest.get("snapshot_id"):
        raise PublicationValidationError(
            f"Несовпадение snapshot_id: в manifest '{manifest.get('snapshot_id')}', а в changes.to '{to_ref.get('snapshot_id')}'"
        )

    parent_id = manifest.get("parent_snapshot_id")
    from_ref = changes.get("from")
    if parent_id is not None:
        if from_ref is None:
            raise PublicationValidationError(
                f"В manifest указан parent_snapshot_id '{parent_id}', но в changes отсутствует baseline (from: null)"
            )
        if from_ref.get("snapshot_id") != parent_id:
            raise PublicationValidationError(
                f"Несовпадение parent_snapshot_id: в manifest '{parent_id}', а в changes.from '{from_ref.get('snapshot_id')}'"
            )
    else:
        if from_ref is not None:
            raise PublicationValidationError(
                f"В manifest parent_snapshot_id равен null, но в changes присутствует from: {from_ref}"
            )
        if changes.get("valid") is not False:
            raise PublicationValidationError(
                "При отсутствии baseline (parent: null) diff.valid обязан быть false"
            )

    # 3. Географические проверки (locations.json и world_reference.json)
    has_locations = "locations" in artifacts
    has_world_ref = "world_reference" in artifacts
    manifest_wref_id = manifest.get("world_ref_id")

    if has_locations or has_world_ref or manifest_wref_id is not None:
        if not has_locations:
            raise PublicationValidationError("При наличии географической привязки в artifacts обязан присутствовать locations")
        if not has_world_ref:
            raise PublicationValidationError("При наличии географической привязки в artifacts обязан присутствовать world_reference")
        if not manifest_wref_id:
            raise PublicationValidationError("В manifest обязан быть указан непустой world_ref_id")

        locations_path = package_dir / artifacts["locations"]
        world_ref_path = package_dir / artifacts["world_reference"]
        loc_data = read_json(locations_path.read_bytes())
        wref_data = read_json(world_ref_path.read_bytes())

        validate_world_reference(wref_data)
        validate_locations(loc_data, world_ref=wref_data)
        if "location_registry" in artifacts:
            load_location_registry(package_dir, manifest, wref_data)

        # Перекрёстные ссылки
        if loc_data.get("snapshot_id") != manifest.get("snapshot_id"):
            raise PublicationValidationError(
                f"Несовпадение snapshot_id между manifest '{manifest.get('snapshot_id')}' "
                f"и locations.json '{loc_data.get('snapshot_id')}'"
            )
        if loc_data.get("world_ref_id") != manifest_wref_id:
            raise PublicationValidationError(
                f"Несовпадение world_ref_id между manifest '{manifest_wref_id}' "
                f"и locations.json '{loc_data.get('world_ref_id')}'"
            )
        if wref_data.get("world_ref_id") != manifest_wref_id:
            raise PublicationValidationError(
                f"Несовпадение world_ref_id между manifest '{manifest_wref_id}' "
                f"и world_reference.json '{wref_data.get('world_ref_id')}'"
            )

        # Покрытие всех персонажей current.json
        curr_char_ids = {c["id"] for c in current.get("characters", [])}
        loc_char_ids = {c["id"] for c in loc_data.get("characters", [])}
        if curr_char_ids != loc_char_ids:
            raise PublicationValidationError(
                f"Неполное покрытие персонажей в locations.json: расхождение {curr_char_ids ^ loc_char_ids}"
            )

        # Покрытие всех построек property.buildings
        curr_bldg_keys = set()
        for b in (current.get("property") or {}).get("buildings", []):
            bid = b.get("id")
            z = b.get("zone")
            curr_bldg_keys.add(f"{z}:{bid}" if z else f"unknown:{bid}")
        loc_bldg_keys = {b["building_key"] for b in loc_data.get("buildings", [])}
        if curr_bldg_keys != loc_bldg_keys:
            raise PublicationValidationError(
                f"Неполное покрытие построек в locations.json: расхождение {curr_bldg_keys ^ loc_bldg_keys}"
            )

        # Согласованность movement и changes
        mov = loc_data.get("movement", {})
        if mov.get("valid") != changes.get("valid"):
            raise PublicationValidationError(
                f"Несогласованность movement.valid ({mov.get('valid')}) и changes.valid ({changes.get('valid')})"
            )
        if changes.get("valid") is True:
            expected_from = changes.get("from", {}).get("snapshot_id")
            if mov.get("from_snapshot_id") != expected_from:
                raise PublicationValidationError(
                    f"Несовпадение from_snapshot_id в movement '{mov.get('from_snapshot_id')}' и changes '{expected_from}'"
                )
            if mov.get("to_snapshot_id") != manifest.get("snapshot_id"):
                raise PublicationValidationError(
                    f"Несовпадение to_snapshot_id в movement '{mov.get('to_snapshot_id')}' и manifest '{manifest.get('snapshot_id')}'"
                )

    # 4. Проверка timeline при наличии
    if "timeline" in artifacts:
        from tools.timeline import read_timeline

        try:
            events = read_timeline(timeline_path, projection=True)
            validate_timeline(events, index_data, projection=True)
            check_privacy(timeline_path.read_text(encoding="utf-8"), "timeline.jsonl")
        except (ValueError, OSError) as error:
            raise PublicationValidationError(f"Некорректная проекция timeline.jsonl: {error}") from error

    # 4.1. Проверка screenshots при наличии
    if "screenshots" in artifacts:
        from tools.screenshots import compute_file_sha256
        sc_rel = artifacts["screenshots"]
        if not sc_rel.endswith(".json"):
            raise PublicationValidationError(f"artifacts.screenshots обязан указывать на JSON-индекс: {sc_rel}")
        sc_path = package_dir / sc_rel
        try:
            sc_data = read_json(sc_path.read_bytes())
            validate(sc_data, "screenshots-index")
        except (ValidationError, Exception) as error:
            raise PublicationValidationError(f"Ошибка валидации схемы screenshots-index: {error}") from error

        sc_items = sc_data.get("screenshots", [])
        if sc_data.get("count") != len(sc_items):
            raise PublicationValidationError(
                f"Несовпадение count ({sc_data.get('count')}) и длины screenshots ({len(sc_items)}) в индексе"
            )

        seen_orders: set[int] = set()
        seen_filenames: set[str] = set()
        for idx, item in enumerate(sc_items, 1):
            ord_num = item.get("order")
            fn = item.get("filename")
            rel_p = item.get("path")
            if ord_num != idx:
                raise PublicationValidationError(f"Нарушен хронологический порядок: ожидался order {idx}, получен {ord_num}")
            if fn in seen_filenames:
                raise PublicationValidationError(f"Повтор имени файла в screenshots: {fn}")
            seen_filenames.add(fn)

            img_p = (package_dir / rel_p).resolve()
            if not img_p.is_relative_to(package_dir):
                raise PublicationValidationError(f"Путь скриншота {rel_p} выходит за пределы пакета")
            if linked(img_p):
                raise PublicationValidationError(f"Скриншот {rel_p} является ссылкой (symlink/junction)")
            if not img_p.is_file():
                raise PublicationValidationError(f"Файл скриншота {rel_p} отсутствует на диске")
            if img_p.stat().st_size != item["size_bytes"]:
                raise PublicationValidationError(
                    f"Несовпадение размера скриншота {fn}: в индексе {item['size_bytes']}, на диске {img_p.stat().st_size}"
                )
            actual_sha = compute_file_sha256(img_p)
            if actual_sha != item["sha256"]:
                raise PublicationValidationError(
                    f"Несовпадение SHA-256 скриншота {fn}: в индексе {item['sha256']}, на диске {actual_sha}"
                )

        # Проверка отсутствия неиндексированных скриншотов
        sc_dir = package_dir / "screenshots"
        if sc_dir.is_dir():
            for entry in sc_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                    if entry.name not in seen_filenames:
                        raise PublicationValidationError(f"Обнаружен неиндексированный файл скриншота: {entry.name}")

    # 4.2. Проверка screenshot_annotations при наличии
    if "screenshot_annotations" in artifacts:
        ann_rel = artifacts["screenshot_annotations"]
        if not ann_rel.endswith(".jsonl"):
            raise PublicationValidationError(f"artifacts.screenshot_annotations обязан указывать на JSONL: {ann_rel}")
        ann_path = package_dir / ann_rel
        if not ann_path.is_file():
            raise PublicationValidationError(f"Файл аннотаций скриншотов отсутствует: {ann_rel}")
        try:
            for line_no, raw_line in enumerate(ann_path.read_text(encoding="utf-8").splitlines(), 1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                record = json.loads(raw_line)
                validate(record, "screenshot-annotations")
        except Exception as error:
            raise PublicationValidationError(f"Ошибка валидации {ann_rel}: {error}") from error

    # 4.3. Проверка screenshot_feed при наличии
    if "screenshot_feed" in artifacts:
        feed_rel = artifacts["screenshot_feed"]
        if not feed_rel.endswith(".md"):
            raise PublicationValidationError(f"artifacts.screenshot_feed обязан указывать на Markdown: {feed_rel}")
        feed_path = package_dir / feed_rel
        if not feed_path.is_file():
            raise PublicationValidationError(f"Файл визуальной ленты отсутствует: {feed_rel}")

    # 4.4. Читаемые облачными моделями файлы не ссылаются на неопубликованные локальные дельты
    for name in ("brief.md", "changes.md", "timeline.jsonl"):
        path = package_dir / name
        if path.is_file() and "data/diffs/" in path.read_text(encoding="utf-8"):
            raise PublicationValidationError(f"В {name} осталась ссылка на неопубликованный каталог data/diffs/")

    # 5. Проверка приватности во всех текстовых файлах пакета
    for root_dir, dirs, files in os.walk(package_dir):
        for name in files:
            file_p = Path(root_dir) / name
            if file_p.name == ".gitkeep" or file_p.suffix.lower() in {".jpg", ".jpeg", ".png", ".pdf"}:
                continue
            text = file_p.read_text(encoding="utf-8", errors="replace")
            check_privacy(text, file_p.relative_to(package_dir).as_posix())

    # 6. Проверка согласованности с индексом data/index.json (при наличии)
    checked_index = False
    if index_data is not None and isinstance(index_data, dict):
        snap_id = manifest.get("snapshot_id")
        idx_snaps = index_data.get("snapshots", {})
        if snap_id and snap_id in idx_snaps:
            idx_entry = idx_snaps[snap_id]
            checked_index = True
            if manifest.get("parent_snapshot_id") != idx_entry.get("parent_snapshot_id"):
                raise PublicationValidationError(
                    f"Несовпадение parent_snapshot_id между manifest '{manifest.get('parent_snapshot_id')}' "
                    f"и data/index.json '{idx_entry.get('parent_snapshot_id')}'"
                )
            if idx_entry.get("campaign_id") is not None and manifest.get("campaign_id") != idx_entry.get("campaign_id"):
                raise PublicationValidationError(
                    f"Несовпадение campaign_id между manifest '{manifest.get('campaign_id')}' "
                    f"и data/index.json '{idx_entry.get('campaign_id')}'"
                )
            if idx_entry.get("world_ref_id") and manifest.get("world_ref_id") != idx_entry.get("world_ref_id"):
                raise PublicationValidationError(
                    f"Несовпадение world_ref_id между manifest '{manifest.get('world_ref_id')}' "
                    f"и data/index.json '{idx_entry.get('world_ref_id')}'"
                )

    return {
        "status": "passed",
        "warnings": package_warnings(package_dir),
        "manifest": manifest,
        "checks": [
            "schema",
            "artifacts",
            "semantics",
            "geography" if (has_locations and has_world_ref) else "no_geography",
            *(["location_registry"] if "location_registry" in artifacts else []),
            *(["unique_entities_registry"] if "unique_entities_registry" in artifacts else []),
            "timeline" if "timeline" in artifacts else "no_timeline",
            "screenshots" if "screenshots" in artifacts else "no_screenshots",
            "screenshot_annotations" if "screenshot_annotations" in artifacts else "no_screenshot_annotations",
            "privacy",
            "index" if checked_index else "no_index",
        ],
    }
