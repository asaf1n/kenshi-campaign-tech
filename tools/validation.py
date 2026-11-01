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


def validate(value: Any, schema_name: str) -> None:
    if schema_name not in {"manifest", "timeline-event", "world-reference"}:
        raise ValueError("Неизвестная проектная схема")
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


def check_examples() -> int:
    count = 0
    for path in sorted((ROOT / "examples").glob("*.json")):
        value = read_json(path.read_bytes())
        fmt = value.get("format")
        if fmt == "kenshi-publication/1":
            validate(value, "manifest")
        elif fmt == "kenshi-world-reference/1":
            validate_world_reference(value)
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

    # 2. Семантические проверки
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

    # 3. Проверка timeline при наличии
    if "timeline" in artifacts:
        timeline_path = package_dir / artifacts["timeline"]
        for line in timeline_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            event = read_json(line.encode("utf-8"))
            validate(event, "timeline-event")

    # 4. Проверка приватности во всех файлах пакета
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
        "checks": ["schema", "artifacts", "semantics", "privacy"],
    }
