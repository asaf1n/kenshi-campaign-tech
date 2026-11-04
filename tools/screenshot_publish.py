"""Подготовка скриншотов к публикации: даунскейл, локальный архив и отбор ключевых кадров.

В выпуск попадают только ключевые кадры (20-30 на окно публикации), уменьшенные до MAX_SIDE по длинной
стороне. Полный уменьшенный набор кадров окна хранится в ARCHIVE_DIR вне git (каталог можно связать
junction с диском, синхронизируемым с облачным хранилищем). Исходный каталог игры остаётся read-only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.screenshots import compute_file_sha256

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "data" / "screenshots_archive"
MAX_SIDE = 1600
# qscale кодека mjpeg: 5 даёт 150-250 КБ на кадр 1600 px (около JPEG q80).
QSCALE = 5
MIN_KEYFRAMES = 20
MAX_KEYFRAMES = 30


def downscale_jpeg(src: Path, dst: Path, max_side: int = MAX_SIDE, qscale: int = QSCALE) -> None:
    """Уменьшает кадр до max_side по длинной стороне и записывает JPEG атомарно (детерминированно)."""
    try:
        import av
    except ImportError as err:
        from tools.publisher import PublisherError
        raise PublisherError("Для публикации скриншотов нужен пакет av (pip install av)") from err

    with av.open(str(src)) as container:
        frame = next(container.decode(video=0))
    scale = min(1.0, max_side / max(frame.width, frame.height))
    width = max(2, round(frame.width * scale) // 2 * 2)
    height = max(2, round(frame.height * scale) // 2 * 2)
    small = frame.reformat(width=width, height=height, format="yuvj420p")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    out = av.open(str(tmp), "w", format="image2")
    try:
        stream = out.add_stream("mjpeg", rate=1)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuvj420p"
        stream.codec_context.options = {"qscale": str(qscale), "flags": "+qscale"}
        stream.codec_context.qmin = stream.codec_context.qmax = qscale
        for packet in stream.encode(small):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    finally:
        out.close()
    os.replace(tmp, dst)


OCR_FIELDS = ("dialogues", "characters", "location", "game_day", "game_time", "notifications", "items_or_tasks")


def has_ocr_content(records: list[dict[str, Any]]) -> bool:
    """Есть ли в каком-либо кадре хотя бы одно непустое поле OCR (`None`, `[]`, `""` считаются пустыми)."""
    return any(
        any(record.get("ocr", {}).get(field) not in (None, [], "") for field in OCR_FIELDS)
        for record in records
    )


def published_name(item: dict[str, Any]) -> str:
    """Имя файла в архиве и выпуске: исходное имя и префикс sha256 оригинала (устойчиво к переиспользованию номеров)."""
    return f"{Path(item['filename']).stem}-{item['sha256'][:8]}.jpg"


def archive_screenshots(items: list[dict[str, Any]], archive_dir: Path | None = None) -> list[dict[str, Any]]:
    """Уменьшает все кадры окна в архив (уже готовые пропускаются) и возвращает копии с полями публикуемого файла."""
    archive_dir = Path(archive_dir or ARCHIVE_DIR)
    result: list[dict[str, Any]] = []
    for item in items:
        name = published_name(item)
        target = archive_dir / name
        if not target.is_file() or target.stat().st_size <= 0:
            downscale_jpeg(Path(item["source_path"]), target)
        enriched = dict(item)
        enriched.update({
            "published_name": name,
            "archive_path": target,
            "published_sha256": compute_file_sha256(target),
            "published_size": target.stat().st_size,
        })
        result.append(enriched)
    return result


def _thin(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Равномерно оставляет не больше limit элементов."""
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:limit]
    return [items[round(i * (len(items) - 1) / (limit - 1))] for i in range(limit)]


def select_keyframes(
    items: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    min_frames: int = MIN_KEYFRAMES,
    max_frames: int = MAX_KEYFRAMES,
    priority: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Детерминированный отбор: по кадру на эпизод, затем равномерно по времени до границ min/max.

    Кадры `priority` (дельта снимка) занимают выпуск первыми, чтобы у них были raw-ссылки; остаток мест заполняется
    обычным отбором. Если дельта больше max_frames, она прореживается равномерно.
    """
    if not items:
        return []
    by_name = {item["filename"]: item for item in items}
    chosen = [by_name[ep["key_filename"]] for ep in episodes if ep.get("key_filename") in by_name]
    if len(chosen) > max_frames:
        chosen = [chosen[round(i * (len(chosen) - 1) / (max_frames - 1))] for i in range(max_frames)]
    elif len(chosen) < min_frames:
        taken = {item["filename"] for item in chosen}
        rest = [item for item in items if item["filename"] not in taken]
        need = min(min_frames - len(chosen), len(rest))
        if need > 0:
            chosen += [rest[round(i * (len(rest) - 1) / max(need - 1, 1))] for i in range(need)] if need > 1 else [rest[len(rest) // 2]]
    seen: set[str] = set()
    unique = [item for item in chosen if not (item["filename"] in seen or seen.add(item["filename"]))]
    first = [by_name[name] for name in priority or [] if name in by_name]
    if first:
        first = _thin(first, max_frames)
        taken = {item["filename"] for item in first}
        unique = first + _thin([item for item in unique if item["filename"] not in taken], max_frames - len(first))
    return sorted(unique, key=lambda item: (item["mtime"], item["filename"]))
