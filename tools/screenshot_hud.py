"""Машинная разметка кадров по тексту постоянного интерфейса Kenshi, без языковых моделей.

Поля ищутся по подписям («День», «Деньги», «Состояние», «Цель»), а не по координатам: оформление и разрешение
кадров менялись. Значения не исправляются и не восстанавливаются по соседним кадрам: загрузка старого сейва законно
откатывает день. Что не прочитано, остаётся `None`. Индекс хранится вне git рядом с архивом кадров.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from tools import screenshot_publish

INDEX_NAME = "hud_index.jsonl"
SCRIPT = Path(__file__).with_name("winocr.ps1")
# Запуск PowerShell и загрузка WinRT занимают секунды, распознавание — доли секунды на кадр.
TIMEOUT_BASE_SECONDS = 60
TIMEOUT_PER_FRAME_SECONDS = 5

DAY = re.compile(r"^[дД][еёЕ]н[а-яёьъ6]{0,2}\s*[:;.,]?\s*(\d{1,4})$")
DAY_LABEL = re.compile(r"^[дД][еёЕ]н[а-яёьъ6]{0,2}\s*[:;.,]")
MONEY_LABEL = re.compile(r"^[дД][еёЕ]ньг")
MONEY = re.compile(r"^[кК][.,]\s*(\d[\d ]*)$")
TIME = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")
STATE_LABEL = re.compile(r"^[сС]остояние\s*[:;]?$")
GOAL_LABEL = re.compile(r"^[цЦ]ель\s*[:;]?$")
DAY_STRIP = re.compile(r"(\d{1,4})$")
# На кадрах 1680×1050 цифровой проход принимал двоеточие за единицу: «День: 4» читалось как 14.
MIN_STRIP_HEIGHT = 1600
TASK = re.compile(r"^\d{1,2}\s*[:;.]\s*([А-ЯЁа-яё].{2,})$")

HUD_FIELDS = ("game_day", "game_time", "money", "selected", "state", "goal", "tasks")


def _first(lines: list[dict[str, Any]], pattern: re.Pattern[str]) -> dict[str, Any] | None:
    return next((line for line in lines if pattern.match(line["text"].strip())), None)


def _parse_clock(lines: list[dict[str, Any]], height: int) -> dict[str, Any]:
    """День, время и деньги: панель из двух строк «Деньги / День», значения справа от подписей."""
    found: dict[str, Any] = {}
    day_line = _first(lines, DAY_LABEL)
    money_line = _first(lines, MONEY_LABEL)
    if day_line:
        match = DAY.match(day_line["text"].strip())
        found["game_day"] = int(match.group(1)) if match else None
    anchor = day_line or money_line
    if not anchor:
        return found
    near = [line for line in lines if abs(line["y"] - anchor["y"]) <= height * 0.04 and line["x"] >= anchor["x"]]
    for line in near:
        text = line["text"].strip()
        clock = TIME.match(text)
        if clock and "game_time" not in found:
            found["game_time"] = f"{int(clock.group(1)):02d}:{clock.group(2)}"
        money = MONEY.match(text)
        if money and "money" not in found:
            found["money"] = int(money.group(1).replace(" ", ""))
    return found


def _parse_panel(lines: list[dict[str, Any]], height: int) -> dict[str, Any]:
    """Левая панель: имя выбранного персонажа над подписью «Состояние», значения справа от подписей."""
    found: dict[str, Any] = {}
    state_line = _first(lines, STATE_LABEL)
    if not state_line:
        return found
    goal_line = _first(lines, GOAL_LABEL)
    if goal_line:
        # OCR смещает столбец значений относительно подписей на высоту строки, поэтому значения берутся по порядку,
        # а не по ближайшей подписи. Одно значение из двух неоднозначно и не присваивается.
        top, bottom = state_line["y"] - 1.5 * state_line["h"], goal_line["y"] + goal_line["h"]
        values = sorted((line for line in lines
                         if line["x"] > state_line["x"] + state_line["w"] and top <= line["y"] <= bottom
                         and not line["text"].strip()[:1].isdigit()), key=lambda line: line["y"])
        if len(values) >= 2:
            found["state"], found["goal"] = values[0]["text"].strip(), values[1]["text"].strip()
    above = [line for line in lines
             if 0 < state_line["y"] - line["y"] <= height * 0.06 and line["x"] <= state_line["x"] + height * 0.03]
    if above:
        found["selected"] = max(above, key=lambda line: line["y"])["text"].strip()
    return found


def _parse_tasks(lines: list[dict[str, Any]]) -> list[str]:
    tasks: list[str] = []
    for line in sorted(lines, key=lambda item: item["y"]):
        match = TASK.match(line["text"].strip())
        if match and match.group(1).strip() not in tasks:
            tasks.append(match.group(1).strip())
    return tasks


def parse_hud(regions: dict[str, list[dict[str, Any]]], height: int) -> dict[str, Any]:
    """Поля интерфейса из строк OCR; `day_strip` — перечитанная цифровой моделью полоса подписи «День»."""
    hud = regions.get("hud") or []
    result: dict[str, Any] = {field: None for field in HUD_FIELDS}
    result.update(_parse_clock(hud, height))
    result.update(_parse_panel(regions.get("left") or [], height))
    result["tasks"] = _parse_tasks(hud)
    if result["game_day"] is None and height >= MIN_STRIP_HEIGHT:
        strip = DAY_STRIP.search(" ".join(line["text"].strip() for line in regions.get("day_strip") or []))
        result["game_day"] = int(strip.group(1)) if strip else None
    return result


def to_ocr(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Поля контракта `ocr` из строки индекса; `None`, если на кадре ничего не прочитано."""
    if not row or not any(row.get(field) not in (None, []) for field in ("game_day", "game_time", "selected", "tasks")):
        return None
    return {
        "game_day": row.get("game_day"),
        "game_time": row.get("game_time"),
        "location": None,
        "characters": [row["selected"]] if row.get("selected") else [],
        "dialogues": [],
        "notifications": [],
        "items_or_tasks": list(row.get("tasks") or []),
    }


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Готовые строки вывода; последняя может быть обрезана, если процесс упал или был остановлен."""
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def run_winocr(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Один процесс PowerShell на пакет. Возвращает распознанные кадры и причину сбоя; готовые кадры при сбое сохраняются."""
    if not paths:
        return {}, None
    powershell = shutil.which("powershell")
    if not powershell:
        return {}, "PowerShell не найден"
    error: str | None = None
    with tempfile.TemporaryDirectory(prefix="kenshi_winocr_") as temp:
        list_file, out_file = Path(temp) / "list.txt", Path(temp) / "out.jsonl"
        list_file.write_text("\n".join(str(path) for path in paths), encoding="utf-8")
        try:
            subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                 "-ListFile", str(list_file), "-OutFile", str(out_file)],
                stdin=subprocess.DEVNULL, capture_output=True, check=True,
                timeout=TIMEOUT_BASE_SECONDS + TIMEOUT_PER_FRAME_SECONDS * len(paths),
            )
        except subprocess.CalledProcessError as failure:
            # Windows PowerShell пишет ошибки в кодировке OEM.
            error = (failure.stderr or b"").decode("cp866", "replace").strip()[-300:] or f"код {failure.returncode}"
        except subprocess.TimeoutExpired:
            error = "превышено время ожидания пакета"
        except OSError as failure:
            error = str(failure)
        records = _read_records(out_file)
    error = error or next((record["error"] for record in records if record.get("error")), None)
    return {record["path"]: record for record in records if not record.get("error") and "regions" in record}, error


def index_path(archive_dir: Path | None = None) -> Path:
    return Path(archive_dir or screenshot_publish.ARCHIVE_DIR) / INDEX_NAME


def load_hud_index(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Строки индекса по sha256 оригинала. Поля пересчитываются из сохранённых строк OCR: разбор дешевле распознавания."""
    path = path or index_path()
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            rows[row["sha256"]] = {**row, **parse_hud(row.get("regions") or {}, row.get("height") or 0)}
        except (json.JSONDecodeError, KeyError, TypeError):
            # Испорченная строка не отключает индекс: кадр будет распознан заново.
            continue
    return rows


def _save(rows: dict[str, dict[str, Any]], path: Path) -> None:
    ordered = sorted(rows.values(), key=lambda row: (row["modified_at"], row["filename"]))
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def update_hud_index(
    items: Iterable[dict[str, Any]], path: Path | None = None, now_iso: str | None = None,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Дополняет индекс кадрами, которых в нём нет (по sha256 оригинала). Возвращает индекс и предупреждение о сбое."""
    path = path or index_path()
    rows = load_hud_index(path)
    pending: dict[str, dict[str, Any]] = {}
    for item in items:
        if item["sha256"] not in rows:
            pending.setdefault(item["sha256"], item)
    if not pending:
        return rows, None
    recognized, error = run_winocr([Path(item["source_path"]) for item in pending.values()])
    missing = sum(str(item["source_path"]) not in recognized for item in pending.values())
    warning = f"OCR: без разметки осталось кадров: {missing}; причина: {error or 'неизвестна'}" if missing else None
    if not recognized:
        return rows, warning
    stamp = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for sha256, item in pending.items():
        record = recognized.get(str(item["source_path"]))
        if record is None:
            continue
        height = record.get("height") or 0
        rows[sha256] = {
            "sha256": sha256,
            "filename": item["filename"],
            "modified_at": item["modified_at"],
            "width": record.get("width"),
            "height": height,
            "extracted_at": stamp,
            "regions": record["regions"],
            **parse_hud(record["regions"], height),
        }
    _save(rows, path)
    return rows, warning


def raw_text(row: dict[str, Any]) -> str:
    return " ".join(line["text"] for lines in (row.get("regions") or {}).values() for line in lines)


def find_frames(rows: Iterable[dict[str, Any]], day: int | None = None, text: str | None = None) -> list[dict[str, Any]]:
    """Линейный поиск по индексу: игровой день и подстрока сырого текста без учёта регистра."""
    needle = text.casefold() if text else None
    found = [row for row in rows
             if (day is None or row.get("game_day") == day) and (needle is None or needle in raw_text(row).casefold())]
    return sorted(found, key=lambda row: (row["modified_at"], row["filename"]))
