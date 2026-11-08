"""Картографическая схема Kenshi: координаты, соседство регионов и автономный экспорт.

Растр areasmap.tga (24-битный RGB, цвет пикселя = индекс записи BIOME_GROUP) не встраивается
ни в один репозиторий. Проверка геометрии читает его локально из data/world/<world_ref_id>.json.
Производные артефакты assets/cartography/regions_map.svg и regions_adjacency.json доставляются
в Kenshi-Compain (docs/reference/) командой deliver-companion вместе с проверкой совместимости
просмотрщика sites/map/index.html. Коридор — цепочка регионов; шаг цепочки существует,
только если у пары есть общая граница в растре. Иначе показывается разрыв.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Any
import warnings
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MIN_COORD = -147456.0
MAX_COORD = 147456.0
RASTER_PIXEL_SIZE = 1152.0
RASTER_GRID_SIZE = 256
ZONE_SIZE = 4608.0
REGION_DISPLAY_NAMES = {"The Iron Trail": "Железный шельф"}
DISCLAIMER = "Схема общих границ регионов по растру areasmap.tga. Это не маршрут."
MAP_TITLE = "Картографическая схема Kenshi"
STATUS_LABELS = {
    "in_location": "в локации", "near_location": "рядом с локацией",
    "in_region": "в регионе", "unknown": "положение неизвестно",
}
GEOMETRY_WORLD_REF_ID = "sha256-4de10504d03d87e137cf00707bd235011a6991966151058f36b9165e0851c155"
RASTER_SOURCE_SHA256 = "5f93a4ccfd2796dc12820d3a3f48a95606182ade626bd4a45260566c704105f2"
GEOMETRY_SVG_SHA256 = "2000e4e46218f0aece2ccc5f7420ab9142935b59c6be7039ff6f3315359ef313"
GEOMETRY_ALGORITHM = "rle-four-neighbors/1"
RASTER_SOURCE_KEY = "data/newland/land/areasmap.tga"
ASSET_DIR = ROOT / "assets" / "cartography"
GEOMETRY_SVG_PATH = ASSET_DIR / "regions_map.svg"
ADJACENCY_PATH = ASSET_DIR / "regions_adjacency.json"
VIEWER_RELATIVE = Path("sites/map/index.html")
DELIVERED_ARTIFACTS = {"regions_map.svg": Path("docs/reference/regions_map.svg"),
                       "regions_adjacency.json": Path("docs/reference/regions_adjacency.json")}
ADJACENCY_FORMAT = "kenshi-region-adjacency/1"
WORLD_SOURCE_ENV = "KENSHI_WORLD_SOURCE"
# Глава XI: Пойма → Гиблые земли. Цепочки заданы регионами, не точками и не локациями.
CHAPTER_XI_CHAINS: dict[str, list[str]] = {
    "Вариант А — через Возрождение": ["Floodlands", "Rebirth", "Deadlands"],
    "Вариант Б — через Руку Окрана (Край Мира)": ["Floodlands", "Arm of Okran", "Deadlands"],
}


class AmbiguousReleaseError(ValueError):
    """Несопоставимые выпуски нельзя выбрать или слить молча."""


def _valid_pair(a: float, b: float, low: float, high: float) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and low <= v < high for v in (a, b))


def world_to_svg(x: float, z: float) -> tuple[float, float] | None:
    if not _valid_pair(x, z, MIN_COORD, MAX_COORD):
        return None
    # nextafter исключает округление допустимой точки в запрещённую границу 256.
    upper = math.nextafter(float(RASTER_GRID_SIZE), 0.0)
    return (min((x - MIN_COORD) / RASTER_PIXEL_SIZE, upper),
            min((z - MIN_COORD) / RASTER_PIXEL_SIZE, upper))


def svg_to_world(svg_x: float, svg_y: float) -> tuple[float, float] | None:
    if not _valid_pair(svg_x, svg_y, 0, RASTER_GRID_SIZE):
        return None
    return svg_x * RASTER_PIXEL_SIZE + MIN_COORD, svg_y * RASTER_PIXEL_SIZE + MIN_COORD


def world_to_grid(x: float, z: float) -> tuple[int, int] | None:
    point = world_to_svg(x, z)
    if point is None:
        return None
    return int(point[0]), RASTER_GRID_SIZE - 1 - int(point[1])


def grid_to_world_center(px: int, py: int) -> tuple[float, float]:
    if not (0 <= px < RASTER_GRID_SIZE and 0 <= py < RASTER_GRID_SIZE):
        raise ValueError("Ячейка вне растра")
    return (RASTER_PIXEL_SIZE * (px + 0.5) + MIN_COORD,
            RASTER_PIXEL_SIZE * (RASTER_GRID_SIZE - 1 - py + 0.5) + MIN_COORD)


def get_region_display_name(name_en: str, default_ru: str = "") -> str:
    return REGION_DISPLAY_NAMES.get(name_en, default_ru or name_en)


# --- Растр и соседство -------------------------------------------------------------

def decode_raster(rle: list[list[int]], size: int = RASTER_GRID_SIZE) -> list[int]:
    cells: list[int] = []
    for index, length in rle:
        if type(index) is not int or type(length) is not int or length <= 0:
            raise ValueError("Некорректная серия RLE")
        if len(cells) + length > size * size:
            raise ValueError("RLE превышает размер растра")
        cells.extend([index] * length)
    if len(cells) != size * size:
        raise ValueError(f"RLE должен содержать {size * size} ячеек")
    return cells


def build_adjacency(cells: list[int], names: dict[int, str],
                    size: int = RASTER_GRID_SIZE) -> dict[str, list[str]]:
    """4-связное соседство по именам регионов; NONE исключён, диагонали не считаются."""
    graph: dict[str, set[str]] = {names[i]: set() for i in set(cells) if names[i] != "NONE"}
    for py in range(size):
        for px in range(size):
            offset = py * size + px
            a = names[cells[offset]]
            for other in ([offset + 1] if px < size - 1 else []) + ([offset + size] if py < size - 1 else []):
                b = names[cells[other]]
                if a != b and "NONE" not in (a, b):
                    graph[a].add(b)
                    graph[b].add(a)
    return {name: sorted(neighbors) for name, neighbors in sorted(graph.items())}


def load_world_source(path: Path) -> dict[str, Any]:
    """Читает полный локальный мир конвертера; возвращает ячейки, палитру и хеши."""
    source = json.loads(path.read_text(encoding="utf-8"))
    if source.get("world_ref_id") != GEOMETRY_WORLD_REF_ID:
        raise ValueError("Несовместимая геометрия: world_ref_id исходного мира не совпадает")
    if source.get("source_hashes", {}).get(RASTER_SOURCE_KEY) != RASTER_SOURCE_SHA256:
        raise ValueError("Несовместимая геометрия: RASTER_SOURCE_SHA256 исходного мира не совпадает")
    raster = source.get("regions_map", {})
    if (raster.get("width"), raster.get("height"), raster.get("pixel_size")) != (
            RASTER_GRID_SIZE, RASTER_GRID_SIZE, RASTER_PIXEL_SIZE):
        raise ValueError("Несовместимая геометрия: размеры исходного растра не совпадают")
    cells = decode_raster([[row["index"], row["length"]] for row in raster.get("rle", [])])
    names = {row["index"]: row["name_en"] for row in source.get("regions", [])}
    if set(cells) - set(names):
        raise ValueError("Несовместимая геометрия: ячейки растра вне палитры")
    return {"cells": cells, "names": names, "regions": source["regions"]}


def default_world_source() -> Path | None:
    """Локальный режим: data/world/<world_ref_id>.json этого репозитория; KENSHI_WORLD_SOURCE переопределяет путь."""
    override = os.environ.get(WORLD_SOURCE_ENV)
    candidate = Path(override) if override else ROOT / "data" / "world" / f"{GEOMETRY_WORLD_REF_ID}.json"
    return candidate if candidate.is_file() else None


def region_at(world_source: dict[str, Any], x: float, z: float) -> str | None:
    grid = world_to_grid(x, z)
    if grid is None:
        return None
    name = world_source["names"][world_source["cells"][grid[1] * RASTER_GRID_SIZE + grid[0]]]
    return None if name == "NONE" else name


def _adjacency_digest(adjacency: dict[str, list[str]]) -> str:
    payload = json.dumps(adjacency, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cells_digest(cells: list[int]) -> str:
    """SHA-256 декодированных ячеек (uint32 LE): фиксирует растр без его хранения."""
    return hashlib.sha256(struct.pack(f"<{len(cells)}I", *cells)).hexdigest()


def adjacency_document(adjacency: dict[str, list[str]], cells: list[int]) -> dict[str, Any]:
    return {"format": ADJACENCY_FORMAT, "world_ref_id": GEOMETRY_WORLD_REF_ID,
            "raster_source_sha256": RASTER_SOURCE_SHA256, "raster_cells_sha256": cells_digest(cells),
            "algorithm": GEOMETRY_ALGORITHM,
            "adjacency_sha256": _adjacency_digest(adjacency), "adjacency": adjacency}


def load_adjacency_document(path: Path = ADJACENCY_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in (("format", ADJACENCY_FORMAT), ("world_ref_id", GEOMETRY_WORLD_REF_ID),
                          ("raster_source_sha256", RASTER_SOURCE_SHA256), ("algorithm", GEOMETRY_ALGORITHM)):
        if document.get(key) != expected:
            raise ValueError(f"Несовместимая геометрия: таблица соседства, поле {key}")
    adjacency = document.get("adjacency")
    if not isinstance(adjacency, dict) or document.get("adjacency_sha256") != _adjacency_digest(adjacency):
        raise ValueError("Несовместимая геометрия: SHA-256 таблицы соседства не совпадает")
    return document


def load_adjacency(path: Path = ADJACENCY_PATH) -> dict[str, list[str]]:
    return load_adjacency_document(path)["adjacency"]


ADJACENCY_GRAPH: dict[str, list[str]] = {}
if ADJACENCY_PATH.is_file():
    ADJACENCY_GRAPH = load_adjacency()


def are_regions_adjacent(reg_a_en: str, reg_b_en: str, graph: dict[str, list[str]] | None = None) -> bool:
    return reg_b_en in (graph if graph is not None else ADJACENCY_GRAPH).get(reg_a_en, [])


def verify_geometry(world_ref_id: str, geometry_path: Path | None = None,
                    *, world_source: Path | None = None) -> dict[str, Any]:
    """Проверяет контуры и таблицу соседства; при world_source — ещё и пересчёт по растру."""
    if world_ref_id != GEOMETRY_WORLD_REF_ID:
        raise ValueError(f"Несовместимая геометрия: world_ref_id {world_ref_id!r}, "
                         f"ожидается {GEOMETRY_WORLD_REF_ID}")
    document = load_adjacency_document()
    adjacency = document["adjacency"]
    if world_source is not None:
        source = load_world_source(world_source)
        if cells_digest(source["cells"]) != document.get("raster_cells_sha256"):
            raise ValueError("Несовместимая геометрия: ячейки исходного растра не совпадают с закреплёнными")
        if build_adjacency(source["cells"], source["names"]) != adjacency:
            raise ValueError("Несовместимая геометрия: таблица соседства не совпадает с растром")
    geometry_path = geometry_path or GEOMETRY_SVG_PATH
    # text=auto в Git допускает CRLF. Хеш закрепляет текст SVG после CRLF/CR -> LF.
    svg_text = geometry_path.read_text(encoding="utf-8")
    if hashlib.sha256(svg_text.encode("utf-8")).hexdigest() != GEOMETRY_SVG_SHA256:
        raise ValueError(f"Несовместимая геометрия: SHA-256 SVG {geometry_path} не совпадает "
                         f"с закреплёнными контурами мира {GEOMETRY_WORLD_REF_ID}")
    return {"svg": ET.fromstring(svg_text), "world_source_verified": world_source is not None}


# --- Просмотрщик -------------------------------------------------------------------

FORBIDDEN_VIEWER_WORDS = ("проходим", "проверен", "безопас", "фанатик", "исследован")
RASTER_MARKERS = ("raster_rle", "_RASTER_DATA", "regions_map.rle", 'id="baseline"', "BASELINE")


def check_viewer_compat(html: str) -> list[str]:
    """Совместимость просмотрщика с доставляемыми артефактами; пустой список — совместим."""
    errors: list[str] = []
    match = re.search(r'<script id="derived" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        return ["Нет блока derived"]
    try:
        derived = json.loads(match[1])
    except ValueError:
        return ["Блок derived не разбирается как JSON"]
    if derived.get("world_ref_id") != GEOMETRY_WORLD_REF_ID:
        errors.append("world_ref_id просмотрщика не совпадает с геометрией")
    if derived.get("graph") != ADJACENCY_GRAPH:
        errors.append("Граф просмотрщика не совпадает с regions_adjacency.json")
    svg_ns = "{http://www.w3.org/2000/svg}"
    paths = []
    for path in ET.fromstring(GEOMETRY_SVG_PATH.read_text(encoding="utf-8")).iter(svg_ns + "path"):
        title = path.find(svg_ns + "title")
        name = re.search(r"\(([^()]*)\)$", title.text or "") if title is not None else None
        paths.append((name[1] if name else None, path.get("d")))
    shown = [(row.get("name_en"), row.get("d")) for row in derived.get("geometry", [])]
    if sorted(shown, key=str) != sorted(paths, key=str):
        errors.append("Контуры просмотрщика не совпадают с regions_map.svg")
    chains = re.search(r"const CHAINS = (\{.*?\});", html, re.S)
    try:
        if not chains or json.loads(chains[1].replace("'", '"')) != CHAPTER_XI_CHAINS:
            errors.append("Цепочки просмотрщика не совпадают с CHAPTER_XI_CHAINS")
    except ValueError:
        errors.append("Цепочки просмотрщика не разбираются")
    errors += [f"Растр в просмотрщике: {marker}" for marker in RASTER_MARKERS if marker in html]
    errors += [f"Недопустимая формулировка: {word}" for word in FORBIDDEN_VIEWER_WORDS if word in html.casefold()]
    for required in ("../../published/", "confirm("):
        if required not in html:
            errors.append(f"В просмотрщике нет {required}")
    return errors


# --- Выпуск ------------------------------------------------------------------------

def validate_release(manifest: dict[str, Any], world: dict[str, Any],
                     locations: dict[str, Any]) -> list[str]:
    """Возвращает блокирующие ошибки; несовместимые выпуски не объединяются."""
    errors: list[str] = []
    for label, document, expected in (("manifest", manifest, "kenshi-publication/1"),
                                      ("world_reference", world, "kenshi-world-reference/1"),
                                      ("locations", locations, "kenshi-locations/1")):
        if not isinstance(document, dict) or document.get("format") != expected:
            errors.append(f"{label}: неподдерживаемый format")
    if errors:
        return errors
    validation = manifest.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        errors.append("manifest: validation.status должен быть passed")
    for key in ("snapshot_id", "world_ref_id", "branch_id", "campaign_id", "game_time", "generated_at"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            errors.append(f"manifest: отсутствует {key}")
    if not manifest.get("world_ref_id") == world.get("world_ref_id") == locations.get("world_ref_id"):
        errors.append("Несовпадение world_ref_id")
    if manifest.get("world_ref_id") != GEOMETRY_WORLD_REF_ID:
        errors.append("world_ref_id не соответствует геометрии")
    if manifest.get("snapshot_id") != locations.get("snapshot_id"):
        errors.append("Несовпадение snapshot_id")
    for key in ("branch_id", "campaign_id"):
        for document in (world, locations):
            if key in document and document[key] != manifest.get(key):
                errors.append(f"Несовпадение {key}")
    for document, key, identity in ((world, "locations", "instance_id"),
                                     (world, "regions", "index"),
                                     (locations, "characters", "id"),
                                     (locations, "spatial_groups", "group_id")):
        rows = document.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict)
                                             or (type(row.get(identity)) is not int if identity == "index"
                                                 else not isinstance(row.get(identity), str) or not row[identity])
                                             for row in rows):
            errors.append(f"Некорректный список {key}")
        elif len({row[identity] for row in rows}) != len(rows):
            errors.append(f"Повтор идентификатора {identity}")
    if errors:
        return errors
    if not world["regions"]:
        errors.append("Пустой справочник регионов")
    if any(not isinstance(row.get("name_en"), str) or not isinstance(row.get("sid"), str)
           or (row.get("name_ru") is not None and not isinstance(row["name_ru"], str))
           for row in world["regions"]):
        errors.append("Некорректный справочник регионов")
    palette = {row.get("name_en") for row in world["regions"]}
    for row in world["locations"]:
        if row.get("region") is not None and row["region"] not in palette:
            errors.append("Регион локации вне справочника регионов")
        if (not all(isinstance(row.get(key), str) and row[key] for key in ("name_en", "kind"))
                or (row.get("region") is not None and (not isinstance(row["region"], str)
                                                        or row["region"] not in ADJACENCY_GRAPH))
                or not isinstance(row.get("name_ru", ""), str)
                or type(row.get("is_secret")) is not bool
                or world_to_svg(row.get("x"), row.get("z")) is None):
            errors.append("Некорректная локация справочника")
    for character in locations["characters"]:
        status = character.get("resolution_status")
        if not isinstance(status, str) or status not in STATUS_LABELS:
            errors.append("Неизвестный resolution_status")
        if not isinstance(character.get("name"), str):
            errors.append("Некорректное имя персонажа")
        character_region = character.get("region")
        if isinstance(character_region, dict) and character_region.get("name_en") not in palette:
            errors.append("Регион персонажа вне справочника регионов")
        if character_region is not None and (not isinstance(character_region, dict)
                                               or not isinstance(character_region.get("name_en"), str)):
            errors.append("Некорректный регион персонажа")
        if status != "unknown":
            coords, region = character.get("coordinates"), character.get("region")
            if (not isinstance(coords, list) or len(coords) != 3
                    or world_to_svg(coords[0], coords[2]) is None
                    or not isinstance(region, dict) or not isinstance(region.get("name_en"), str)
                    or region["name_en"] not in ADJACENCY_GRAPH):
                errors.append("Некорректное положение персонажа")
    character_ids = {row["id"] for row in locations["characters"]}
    for group in locations["spatial_groups"]:
        members = group.get("characters")
        if (not isinstance(group.get("resolution_status"), str) or group["resolution_status"] not in STATUS_LABELS
                or not isinstance(members, list)
                or any(not isinstance(m, dict) or not isinstance(m.get("id"), str)
                       or m["id"] not in character_ids for m in members)):
            errors.append("Некорректный состав пространственной группы")
    return errors


def verify_release_against_source(release: dict[str, Any], world_source: Path) -> None:
    """Локальный режим: палитра и регионы выпуска сверяются с полным миром и растром."""
    source = load_world_source(world_source)
    identity = lambda rows: {(r["index"], r["sid"], r["name_en"]) for r in rows}  # noqa: E731
    if identity(release["world"]["regions"]) != identity(source["regions"]):
        raise ValueError("Несовместимая геометрия: палитра регионов выпуска не совпадает с миром")
    for row in release["world"]["locations"]:
        if row.get("region") and region_at(source, row["x"], row["z"]) != row["region"]:
            raise ValueError(f"Несовместимая геометрия: регион локации {row['instance_id']} не совпадает с растром")
    for row in release["locations"]["characters"]:
        if row["resolution_status"] != "unknown":
            x, _, z = row["coordinates"]
            if region_at(source, x, z) != row["region"]["name_en"]:
                raise ValueError(f"Несовместимая геометрия: регион персонажа {row['id']} не совпадает с растром")


def load_release(manifest: dict[str, Any], world: dict[str, Any],
                 locations: dict[str, Any]) -> dict[str, Any]:
    errors = validate_release(manifest, world, locations)
    if errors:
        message = "; ".join(errors)
        warnings.warn(message, UserWarning, stacklevel=2)
        raise ValueError(message)
    return {"manifest": manifest, "world": world, "locations": locations}


def load_published(published: Path) -> dict[str, Any]:
    """Текущий выпуск: три файла published/ целиком, без подмешивания других снимков."""
    docs = [json.loads((published / name).read_text(encoding="utf-8"))
            for name in ("manifest.json", "world_reference.json", "locations.json")]
    return load_release(*docs)


def select_release(candidates: list[dict[str, Any]], *, snapshot_id: str | None = None) -> dict[str, Any]:
    """Выбор выпуска. Разные снимки, ветки или кампании не сливаются: нужен явный snapshot_id."""
    if not candidates:
        raise ValueError("Нет выпусков")
    keys = {(r["manifest"]["campaign_id"], r["manifest"]["branch_id"], r["manifest"]["snapshot_id"])
            for r in candidates}
    if snapshot_id is None:
        if len(keys) > 1:
            raise AmbiguousReleaseError("Несопоставимые выпуски (кампания, ветка или снимок): "
                                        "укажите snapshot_id явно")
        return candidates[0]
    chosen = [r for r in candidates if r["manifest"]["snapshot_id"] == snapshot_id]
    if len(chosen) != 1:
        raise AmbiguousReleaseError("snapshot_id не определяет единственный выпуск")
    return chosen[0]


def visible_locations(world: dict[str, Any], *, show_secrets: bool = False,
                      query: str = "", kind: str = "", region: str = "") -> list[dict[str, Any]]:
    return [row for row in world["locations"]
            if (show_secrets or not row.get("is_secret", False))
            and (not kind or row.get("kind") == kind)
            and (not region or row.get("region") == region)
            and query.casefold() in (row.get("name_ru", "") + " " + row.get("name_en", "")).casefold()]


# --- Коридоры ----------------------------------------------------------------------

def compare_corridors(chains: dict[str, list[str]] | None = None,
                      graph: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Шаг цепочки — пара регионов. Нет общей границы в растре → status=gap, цепь не достраивается."""
    graph = graph if graph is not None else ADJACENCY_GRAPH
    result: list[dict[str, Any]] = []
    for name, regions in (chains if chains is not None else CHAPTER_XI_CHAINS).items():
        if len(regions) < 2:
            raise ValueError("Цепочка должна содержать минимум два региона")
        if any(region not in graph for region in regions):
            raise ValueError("Неизвестный регион цепочки")
        steps = [{"from_region": a, "to_region": b,
                  "status": "border" if are_regions_adjacent(a, b, graph) else "gap"}
                 for a, b in zip(regions, regions[1:])]
        result.append({"name": name, "regions": list(regions), "steps": steps,
                       "complete": all(step["status"] == "border" for step in steps)})
    return result


def _region_names(world: dict[str, Any]) -> dict[str, str]:
    return {r["name_en"]: get_region_display_name(r["name_en"], r.get("name_ru", "")) for r in world["regions"]}


def _step_text(step: dict[str, str], names: dict[str, str]) -> str:
    label = "общая граница" if step["status"] == "border" else "разрыв: общей границы нет"
    return f"{names.get(step['from_region'], step['from_region'])} → {names.get(step['to_region'], step['to_region'])}: {label}"


def generate_summary(release: dict[str, Any], corridors: list[dict[str, Any]] | None = None) -> str:
    manifest, world, locations = release["manifest"], release["world"], release["locations"]
    load_release(manifest, world, locations)
    names = _region_names(world)
    lines = [f"## {MAP_TITLE}", f"{manifest['game_time']} · {manifest['campaign_id']} · {manifest['branch_id']}",
             f"world_ref_id: {manifest['world_ref_id']}", f"snapshot_id: {manifest['snapshot_id']}",
             f"Персонажей: {len(locations['characters'])}"]
    for row in locations["characters"]:
        region = (row.get("region") or {}).get("name_en", "")
        lines.append(f"- {row['name']} ({row['id']}): {STATUS_LABELS[row['resolution_status']]} — {names.get(region, region) or 'нет данных'}")
    for corridor in corridors or []:
        lines.append(f"- {corridor['name']}:")
        lines += [f"  - {_step_text(step, names)}" for step in corridor["steps"]]
    return "\n".join(lines + ["", DISCLAIMER])


def export_svg(release: dict[str, Any], corridors: list[dict[str, Any]] | None = None,
               *, show_secrets: bool = False, query: str = "", kind: str = "", region: str = "",
               geometry_path: Path | None = None) -> str:
    manifest, world, locations = release["manifest"], release["world"], release["locations"]
    load_release(manifest, world, locations)
    geometry = verify_geometry(manifest["world_ref_id"], geometry_path)["svg"]
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    svg_ns = "{http://www.w3.org/2000/svg}"

    def element(tag: str, attrs: dict[str, str] | None = None, text: str | None = None) -> ET.Element:
        node = ET.Element(svg_ns + tag, attrs or {})
        node.text = text
        return node
    names = _region_names(world)
    stamps = {key: manifest[key] for key in ("world_ref_id", "snapshot_id", "branch_id", "game_time", "campaign_id")}
    footer = [MAP_TITLE, "Источник: published/manifest.json + world_reference.json + locations.json"]
    footer += [f"{key}: {value}" for key, value in stamps.items() if key != "campaign_id"]
    footer.append(f"Фильтры: секретные={show_secrets}; поиск={query}; тип={kind}; регион={get_region_display_name(region)}")
    for corridor in corridors or []:
        footer.append(corridor["name"])
        footer += [f"  {_step_text(step, names)}" for step in corridor["steps"]]
    footer.append(DISCLAIMER)
    root = element("svg", {"viewBox": f"0 0 256 {262 + len(footer) * 3.7:.1f}", "width": "1024"})
    root.append(element("title", text=MAP_TITLE))
    stamps.update(source="published/manifest.json + world_reference.json + locations.json", show_secrets=show_secrets,
                  query=query, kind=kind, region=region,
                  geometry_world_ref_id=GEOMETRY_WORLD_REF_ID, raster_source_sha256=RASTER_SOURCE_SHA256,
                  geometry_svg_sha256=GEOMETRY_SVG_SHA256, geometry_algorithm=GEOMETRY_ALGORITHM)
    root.append(element("metadata", text=json.dumps(stamps, ensure_ascii=False)))
    # Название-псевдоним применяется ко всем подписям исходной схемы.
    for node in geometry.iter():
        if node.text:
            node.text = node.text.replace("Железный шлейф", "Железный шельф")
    layer = element("g", {"id": "regions"})
    layer.extend(list(geometry))
    root.append(layer)
    for row in visible_locations(world, show_secrets=show_secrets, query=query, kind=kind, region=region):
        point = world_to_svg(row.get("x"), row.get("z"))
        if point:
            marker = element("circle", {"cx": str(point[0]), "cy": str(point[1]), "r": "0.65", "fill": "#26353b"})
            marker.append(element("title", text=row.get("name_ru") or row["name_en"]))
            root.append(marker)
    for row in locations["characters"]:
        coords = row.get("coordinates")
        point = world_to_svg(coords[0], coords[2]) if isinstance(coords, list) and len(coords) == 3 else None
        if row["resolution_status"] != "unknown" and point:
            color = {"in_location": "#19764b", "near_location": "#b36a13", "in_region": "#235fa4"}[row["resolution_status"]]
            marker = element("circle", {"cx": str(point[0]), "cy": str(point[1]), "r": "1.0", "fill": color})
            marker.append(element("title", text=f"{row['name']}: {STATUS_LABELS[row['resolution_status']]}"))
            root.append(marker)
    # Регионы цепочек обводятся контуром; линий между регионами нет, разрыв не соединяется.
    outlines = element("g", {"id": "corridors"})
    colors = ["#9c3153", "#325baf"]
    paths = list(geometry.iter(svg_ns + "path"))
    for i, corridor in enumerate(corridors or []):
        for name in corridor["regions"]:
            for path in paths:
                title = path.find(svg_ns + "title")
                if title is not None and title.text and title.text.endswith(f"({name})"):
                    outline = element("path", {"d": path.get("d", ""), "fill": "none", "stroke": colors[i % 2],
                                               "stroke-width": "0.8", "stroke-dasharray": "2 1"})
                    outline.append(element("title", text=f"{corridor['name']}: {names.get(name, name)}"))
                    outlines.append(outline)
    root.append(outlines)
    for i, line in enumerate(footer):
        root.append(element("text", {"x": "2", "y": str(260 + i * 3.7), "font-size": "2.1", "fill": "#18252a"}, line))
    return ET.tostring(root, encoding="unicode")


def main() -> None:
    parser = argparse.ArgumentParser(description=MAP_TITLE)
    parser.add_argument("--published", type=Path, default=ROOT / "published")
    parser.add_argument("--svg", type=Path, help="Записать схему в SVG")
    parser.add_argument("--verify-geometry", action="store_true", help="Проверить геометрию без экспорта")
    parser.add_argument("--geometry", type=Path, help="Путь к проверяемому SVG контуров")
    parser.add_argument("--world-source", type=Path, help="Полный локальный data/world/<world_ref_id>.json")
    parser.add_argument("--local", action="store_true",
                        help=f"Локальный режим: data/world/<world_ref_id>.json или {WORLD_SOURCE_ENV}")
    parser.add_argument("--build-adjacency", action="store_true",
                        help="Пересчитать docs/reference/regions_adjacency.json по --world-source")
    args = parser.parse_args()
    if args.build_adjacency:
        if args.world_source is None:
            parser.exit(2, "--build-adjacency требует --world-source\n")
        source = load_world_source(args.world_source)
        document = adjacency_document(build_adjacency(source["cells"], source["names"]), source["cells"])
        ADJACENCY_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                  encoding="utf-8")
        return
    release = load_published(args.published)
    if args.local and args.world_source is None:
        args.world_source = default_world_source()
        if args.world_source is None:
            parser.exit(1, "Ошибка проверки геометрии: полный мир для --local не найден\n")
    try:
        report = verify_geometry(release["manifest"]["world_ref_id"], args.geometry, world_source=args.world_source)
        if args.world_source is not None:
            verify_release_against_source(release, args.world_source)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as exc:
        parser.exit(1, f"Ошибка проверки геометрии: {exc}\n")
    if args.verify_geometry:
        print(json.dumps({"format": "kenshi-cartography-verification/1", "status": "passed",
                          "world_ref_id": GEOMETRY_WORLD_REF_ID,
                          "raster_source_sha256": RASTER_SOURCE_SHA256,
                          "adjacency_sha256": _adjacency_digest(ADJACENCY_GRAPH),
                          "raster_cells_sha256": load_adjacency_document()["raster_cells_sha256"],
                          "geometry_svg_sha256": GEOMETRY_SVG_SHA256,
                          "algorithm": GEOMETRY_ALGORITHM,
                          "world_source_verified": report["world_source_verified"]}, ensure_ascii=False))
        return
    corridors = compare_corridors()
    if args.svg:
        args.svg.write_text(export_svg(release, corridors, geometry_path=args.geometry), encoding="utf-8")
    else:
        print(generate_summary(release, corridors))


if __name__ == "__main__":
    main()
