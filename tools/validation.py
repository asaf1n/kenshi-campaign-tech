"""Проверка проектных схем Draft 2020-12 и валидация пакета публикации."""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from tools.io_utils import linked, read_json
from tools.normalization import _AUTH, _PATH, _SECRET_TEXT, _TOKEN

ROOT = Path(__file__).resolve().parents[1]


class PublicationValidationError(ValueError):
    """Отказ валидации пакета публикации."""


class WorldReferenceValidationError(ValueError):
    """Отказ валидации справочника мира."""


class LocationsValidationError(ValueError):
    """Отказ валидации пространственного разрешения локаций."""


def validate(value: Any, schema_name: str) -> None:
    if schema_name not in {"manifest", "timeline-event", "world-reference", "locations"}:
        raise ValueError(f"Неизвестная проектная схема: {schema_name}")
    schema = read_json((ROOT / "contracts" / f"{schema_name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def validate_manifest(manifest: dict[str, Any]) -> None:
    validate(manifest, "manifest")


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

    # 4. Сумма длин RLE-сегментов строго равна 65536
    rle = world_ref.get("regions_map", {}).get("rle", [])
    total_len = sum(seg["length"] for seg in rle)
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

    for seg in rle:
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
    count = 0
    for path in sorted((ROOT / "examples").glob("*.json")):
        value = read_json(path.read_bytes())
        fmt = value.get("format")
        if fmt == "kenshi-publication/1":
            validate(value, "manifest")
        elif fmt == "kenshi-world-reference/1":
            validate_world_reference(value)
        elif fmt == "kenshi-locations/1":
            validate_locations(value)
        else:
            validate(value, "timeline-event")
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


def validate_publication(package_dir: Path) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file() or linked(manifest_path):
        raise PublicationValidationError("В пакете отсутствует обязательный manifest.json")

    try:
        manifest = read_json(manifest_path.read_bytes())
        validate_manifest(manifest)
    except (ValidationError, Exception) as error:
        raise PublicationValidationError(f"Ошибка валидации схемы manifest.json: {error}") from error

    # 1. Проверка артефактов
    artifacts = manifest.get("artifacts", {})
    required_artifacts = {"current", "changes", "brief", "squad"}
    if not required_artifacts.issubset(artifacts.keys()):
        missing = required_artifacts - set(artifacts.keys())
        raise PublicationValidationError(f"В artifacts отсутствуют обязательные файлы: {missing}")

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

    # 2. Семантические проверки current и changes
    current_path = package_dir / artifacts["current"]
    changes_path = package_dir / artifacts["changes"]
    current = read_json(current_path.read_bytes())
    changes = read_json(changes_path.read_bytes())

    if current.get("format") != "kenshi-session/10":
        raise PublicationValidationError(f"Некорректный формат current.json: {current.get('format')}")
    if changes.get("format") != "kenshi-session-diff/5":
        raise PublicationValidationError(f"Некорректный формат changes.json: {changes.get('format')}")

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
        timeline_path = package_dir / artifacts["timeline"]
        for line in timeline_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            event = read_json(line.encode("utf-8"))
            validate(event, "timeline-event")

    # 5. Проверка приватности во всех файлах пакета
    for root_dir, dirs, files in os.walk(package_dir):
        for name in files:
            file_p = Path(root_dir) / name
            if file_p.name == ".gitkeep":
                continue
            text = file_p.read_text(encoding="utf-8", errors="replace")
            check_privacy(text, file_p.relative_to(package_dir).as_posix())

    return {
        "status": "passed",
        "manifest": manifest,
        "checks": [
            "schema",
            "artifacts",
            "semantics",
            "geography" if (has_locations and has_world_ref) else "no_geography",
            "privacy",
        ],
    }
