"""Разделение кадров на дельту и архивный хвост, привязка к снимкам и первичная разметка.

Разметка собирается только из данных конвертера: интервал между снимками по времени регистрации, изменения дельты
этого интервала, расположение групп по ближайшему снимку и OCR при наличии. Это ориентиры для модели: файловое
время кадра не доказывает, что кадр показывает эти события.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.chain_timeline import chain_ids, significant_changes
from tools.config import GDRIVE_SESSION_DIR
from tools.screenshots import RAW_DEFAULT_URL

TAIL_FRAMES = 12
PDF_FALLBACK_FRAMES = 5
MARKUP_EVENTS = 6
MARKUP_GROUPS = 3
PDF_NAME = "screenshots_latest.pdf"

NO_FRAMES_NOTE = "Новых кадров после предыдущего снимка нет."


def snapshot_chain(index_data: dict[str, Any] | None, snapshot_id: str) -> list[dict[str, Any]]:
    """Записи индекса окна цепочки от старого снимка к текущему, упорядоченные по времени регистрации."""
    if not index_data or snapshot_id not in index_data.get("snapshots", {}):
        return []
    entries = [index_data["snapshots"][sid] for sid in chain_ids(index_data, snapshot_id)]
    return sorted((entry for entry in entries if entry.get("registered_at")), key=lambda entry: entry["registered_at"])


def delta_boundary(index_data: dict[str, Any] | None, snapshot_id: str, parent_id: str | None) -> str | None:
    """Время регистрации ближайшего предка с другим сейвом.

    Повторный запуск на том же сейве после правки конвертера даёт новый снимок (`transform_sha256`) с тем же
    источником. Граница по прямому родителю обнуляла бы дельту, и обязательные к анализу кадры исчезали бы.
    """
    snapshots = (index_data or {}).get("snapshots", {})
    source = (snapshots.get(snapshot_id) or {}).get("source_sha256")
    seen = {snapshot_id}
    while parent_id in snapshots and parent_id not in seen:
        entry = snapshots[parent_id]
        if source is None or entry.get("source_sha256") != source:
            return entry.get("registered_at")
        seen.add(parent_id)
        parent_id = entry.get("parent_snapshot_id")
    return None


def new_frame_names(items: Iterable[dict[str, Any]], boundary: str | None) -> list[str]:
    """Кадры позже границы дельты; без границы дельта не определена."""
    if not boundary:
        return []
    return [item["filename"] for item in items if item["modified_at"] > boundary]


def game_label(entry: dict[str, Any] | None) -> str:
    """Игровое время записи индекса: `Day 132, 11:15`."""
    game_time = (entry or {}).get("game_time") or {}
    if game_time.get("day") is None:
        return "время не получено"
    return f"Day {game_time['day']}, {game_time.get('time')}"


def _short(entry: dict[str, Any]) -> str:
    return str(entry["snapshot_id"])[:15]


def bind_interval(modified_at: str, chain: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Соседние снимки вокруг файлового времени кадра: последний зарегистрированный до него и первый после."""
    position = bisect_right([entry["registered_at"] for entry in chain], modified_at)
    return (chain[position - 1] if position else None, chain[position] if position < len(chain) else None)


def interval_label(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    if before and after:
        return f"{game_label(before)} ({_short(before)}) → {game_label(after)} ({_short(after)})"
    if before:
        return f"после {game_label(before)} ({_short(before)}); следующий снимок ещё не зарегистрирован"
    if after:
        return f"до {game_label(after)} ({_short(after)})"
    return "снимки окна не найдены"


def _nearest(modified_at: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any] | None:
    if not before or not after:
        return before or after
    moment = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))

    def distance(entry: dict[str, Any]) -> float:
        return abs((datetime.fromisoformat(entry["registered_at"].replace("Z", "+00:00")) - moment).total_seconds())

    return before if distance(before) <= distance(after) else after


def _groups(locations: dict[str, Any] | None) -> str | None:
    groups = sorted((locations or {}).get("spatial_groups") or [],
                    key=lambda group: (-len(group.get("characters") or []), str(group.get("label") or "")))
    labels = [f"{group.get('label')} ({len(group.get('characters') or [])})" for group in groups[:MARKUP_GROUPS]]
    return ", ".join(labels) or None


def _ocr_labels(annotation: dict[str, Any] | None) -> str | None:
    ocr = (annotation or {}).get("ocr") or {}
    labels = [f"День {ocr['game_day']}"] if ocr.get("game_day") is not None else []
    labels += [str(ocr["game_time"])] if ocr.get("game_time") else []
    labels += [str(ocr["location"])] if ocr.get("location") else []
    labels += [str(value) for value in (ocr.get("characters") or [])[:MARKUP_GROUPS]]
    labels += [str(value) for value in (ocr.get("notifications") or [])[:MARKUP_GROUPS]]
    labels += [str(value) for value in (ocr.get("items_or_tasks") or [])[:MARKUP_GROUPS]]
    return ", ".join(labels) or None


def frame_markup(
    modified_at: str,
    chain: list[dict[str, Any]],
    load_diff: Callable[[str, str], dict[str, Any] | None] | None,
    load_locations: Callable[[str], dict[str, Any] | None],
    annotation: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    """Интервал кадра, строка первичной разметки и ближайший снимок."""
    before, after = bind_interval(modified_at, chain)
    nearest = _nearest(modified_at, before, after)
    parts: list[str] = []
    if nearest:
        groups = _groups(load_locations(nearest["snapshot_id"]))
        if groups:
            parts.append(f"группы на {game_label(nearest)}: {groups}")
    if before and after and load_diff:
        diff = load_diff(before["snapshot_id"], after["snapshot_id"])
        summaries = [summary for _, summary in significant_changes(diff or {})]
        if summaries:
            tail = f"; и ещё {len(summaries) - MARKUP_EVENTS}" if len(summaries) > MARKUP_EVENTS else ""
            parts.append("дельта интервала: " + "; ".join(summaries[:MARKUP_EVENTS]) + tail)
    ocr = _ocr_labels(annotation)
    if ocr:
        parts.append(f"OCR: {ocr}")
    return interval_label(before, after), " · ".join(parts) or "ориентиров нет", nearest


def frame_record(
    item: dict[str, Any],
    chain: list[dict[str, Any]],
    load_diff: Callable[[str, str], dict[str, Any] | None] | None,
    load_locations: Callable[[str], dict[str, Any] | None],
    annotation: dict[str, Any] | None,
    in_bundle: bool,
) -> dict[str, Any]:
    """Строка таблицы и шапка страницы PDF для кадра; `item` несёт поля `archive_screenshots`."""
    name = item["published_name"]
    interval, markup, nearest = frame_markup(item["modified_at"], chain, load_diff, load_locations, annotation)
    stamp = item["modified_at"].replace("T", " ").removesuffix("Z")
    snapshot = f"{game_label(nearest)} ({nearest['snapshot_id']})" if nearest else "не определён"
    return {
        "frame_id": Path(item["filename"]).stem,
        "filename": name,
        "sha256": item["published_sha256"],
        "modified_at": item["modified_at"],
        "interval": interval,
        "markup": markup,
        "url_raw": f"{RAW_DEFAULT_URL}/published/screenshots/{name}" if in_bundle else None,
        "drive_path": f"{GDRIVE_SESSION_DIR}/screenshots/{name}",
        "source_path": item["source_path"],
        "pdf_header": f"Кадр: {name} | Файл: {stamp} UTC | Снимок: {snapshot} | Первичная разметка: {markup}",
    }


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_frames_table(frames: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| ID кадра | Файл | sha256 | modified_at (системное время файла, не игровое) | Снимок / интервал | Первичная разметка | Raw | Путь (GitHub и Google Диск) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for frame in frames:
        raw = f"[raw]({frame['url_raw']})" if frame["url_raw"] else "только PDF и Google Диск"
        lines.append(
            f"| `{frame['frame_id']}` | `{frame['filename']}` | `{frame['sha256'][:8]}` `{frame['sha256']}` "
            f"| `{frame['modified_at']}` | {_cell(frame['interval'])} | {_cell(frame['markup'])} "
            f"| {raw} | `{frame['drive_path']}` |"
        )
    return lines


MARKUP_NOTE = ("Первичная разметка — автоматические ориентиры по файловому времени и OCR интерфейса, а не подтверждённые события; "
               "содержание кадра определяется только по изображению.")


def render_brief_section(new_frames: list[dict[str, Any]], total: int) -> str:
    """Секция `## Скриншоты снимка` для brief.md: только кадры дельты, обязательные к анализу на шаге `[ход]`."""
    lines = ["## Скриншоты снимка", ""]
    if new_frames:
        lines += [f"Новых кадров текущего снимка: {len(new_frames)}. Их визуальный анализ обязателен на шаге `[ход]`. {MARKUP_NOTE}", ""]
        lines += render_frames_table(new_frames)
    else:
        lines.append(NO_FRAMES_NOTE)
    lines += ["", f"Архивный хвост и все {total} кадров выпуска: [screenshots/index.md](screenshots/index.md).", ""]
    return "\n".join(lines)


def render_index_md(index_data: dict[str, Any], new_frames: list[dict[str, Any]], tail: list[dict[str, Any]]) -> str:
    """Индекс кадров для читателей без доступа к .json: дельта снимка и архивный хвост."""
    lines = [
        "# Индекс скриншотов Kenshi",
        "",
        f"- **Обновлено:** `{index_data['generated_at']}`",
        f"- **Окно выборки:** {index_data['window_days']} суток (с `{index_data['window_start']}` по `{index_data['window_end']}`)",
        f"- **Кадров в выпуске:** {index_data['count']}",
        f"- **Пакет для просмотра на Google Диске:** `{GDRIVE_SESSION_DIR}/screenshots/{PDF_NAME}`",
        "",
        MARKUP_NOTE,
        "",
        "## А. Новые кадры текущего снимка",
        "",
    ]
    lines += render_frames_table(new_frames) if new_frames else [NO_FRAMES_NOTE]
    lines += ["", "## Б. Архивный хвост", ""]
    lines += render_frames_table(tail) if tail else ["Кадров в архивном хвосте нет."]
    return "\n".join(lines) + "\n"
