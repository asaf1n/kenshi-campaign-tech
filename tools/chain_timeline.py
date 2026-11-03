"""Накопительный таймлайн по цепочке снимков и детектор смены ветки.

События восстанавливаются детерминированно из дельт между последовательными снимками ветки (родитель ->
потомок) и не требуют отдельного журнала. Статус `observed`: факты вычислены по данным сохранений. В выгрузку
попадает окно из последних WINDOW_EDGES переходов; полная цепочка остаётся в `data/` конвертера.
"""
from __future__ import annotations

import re
from typing import Any, Callable

WINDOW_EDGES = 20
NAMES_IN_SUMMARY = 6


def _minutes(game_time: dict[str, Any] | None) -> int | None:
    if not game_time or game_time.get("day") is None:
        return None
    hours, _, minutes = str(game_time.get("time") or "0:0").partition(":")
    try:
        return int(game_time["day"]) * 1440 + int(hours) * 60 + int(minutes or 0)
    except ValueError:
        return None


def chain_ids(index_data: dict[str, Any], snapshot_id: str, window: int = WINDOW_EDGES) -> list[str]:
    """Идентификаторы от самого старого снимка окна к текущему по цепочке родителей индекса."""
    ids = [snapshot_id]
    seen = {snapshot_id}
    while len(ids) <= window:
        parent = index_data.get("snapshots", {}).get(ids[-1], {}).get("parent_snapshot_id")
        if not parent or parent in seen or parent not in index_data.get("snapshots", {}):
            break
        ids.append(parent)
        seen.add(parent)
    return list(reversed(ids))


def detect_branch_switch(
    index_data: dict[str, Any], parent_id: str | None, snapshot_id: str, branch_id: str | None,
) -> dict[str, str] | None:
    """Смена ветки: откат игрового времени относительно родителя либо родитель из другой ветки (форк).

    Линейное продолжение ветки (родитель той же ветки, время не убывает) сменой не считается, поэтому результат
    не зависит от того, какие снимки уже были опубликованы.
    """
    snapshots = index_data.get("snapshots", {})
    if parent_id is None or parent_id not in snapshots or snapshot_id not in snapshots or not branch_id:
        return None
    parent, child = snapshots[parent_id], snapshots[snapshot_id]
    before, after = _minutes(parent.get("game_time")), _minutes(child.get("game_time"))
    if before is not None and after is not None and after < before:
        reason = "time_rollback"
    elif parent.get("branch_id") not in (None, branch_id):
        reason = "parent_mismatch"
    else:
        return None
    return {
        "from_branch": str(parent.get("branch_id") or ""), "to_branch": branch_id,
        "from_snapshot": parent_id, "to_snapshot": snapshot_id, "reason": reason,
    }


def _names(entries: list[dict[str, Any]]) -> str:
    names = [str(entry.get("name") or entry.get("id")) for entry in entries]
    tail = f" и ещё {len(names) - NAMES_IN_SUMMARY}" if len(names) > NAMES_IN_SUMMARY else ""
    return ", ".join(names[:NAMES_IN_SUMMARY]) + tail


def _game_clock(game_time: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """День и `Day N HH:MM` из записи индекса снимка; неполное время даёт None (поле события опускается)."""
    if not game_time or not isinstance(game_time.get("day"), int):
        return None, None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(game_time.get("time") or ""))
    day = game_time["day"]
    return day, (f"Day {day} {int(match.group(1)):02d}:{match.group(2)}" if match else None)


def events_from_diff(
    diff: dict[str, Any], child_id: str, diff_id: str, branch_id: str, recorded_at: str,
    game_time: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Значимые изменения дельты в событиях kenshi-event/1: состав, перестановки, протезы, казна."""
    world = diff.get("world", {})
    day, clock = _game_clock(game_time)
    roster = diff.get("roster") or {}
    found: list[tuple[str, str]] = []
    if roster.get("joined"):
        found.append(("squad_change", f"В отряд вступили: {_names(roster['joined'])}"))
    if roster.get("left"):
        found.append(("squad_change", f"Покинули отряд: {_names(roster['left'])}"))
    if roster.get("squad_changed"):
        found.append(("squad_change", f"Сменили отряд ({len(roster['squad_changed'])}): {_names(roster['squad_changed'])}"))
    if diff.get("prosthetics"):
        found.append(("state_delta", f"Изменены протезы: {_names(diff['prosthetics'])}"))
    money = world.get("money") or {}
    if money.get("delta"):
        found.append(("state_delta", f"Казна: {money.get('from')} -> {money.get('to')} ({money['delta']:+})"))

    prefix = child_id.removeprefix("sha256-")[:16]
    events = []
    for ordinal, (kind, summary) in enumerate(found, 1):
        event = {
            "format": "kenshi-event/1", "event_id": f"auto-{prefix}-{ordinal:02d}", "snapshot_id": child_id,
            "session_id": f"auto-{branch_id}", "recorded_at": recorded_at, "kind": kind, "status": "observed",
            "summary": summary, "branch_id": branch_id, "evidence": [{"kind": "diff", "ref": f"data/diffs/{diff_id}.json"}],
        }
        if day is not None:
            event["game_day"] = day
        if clock:
            event["game_time"] = clock
        events.append(event)
    return events


def branch_switch_event(switch: dict[str, str], recorded_at: str, diff_id: str) -> dict[str, Any]:
    prefix = switch["to_snapshot"].removeprefix("sha256-")[:16]
    return {
        "format": "kenshi-event/1", "event_id": f"auto-{prefix}-switch", "snapshot_id": switch["to_snapshot"],
        "session_id": f"auto-{switch['to_branch']}", "recorded_at": recorded_at, "kind": "branch_switch",
        "status": "observed", "branch_id": switch["to_branch"],
        "summary": f"Смена ветки {switch['from_branch']} -> {switch['to_branch']} ({switch['reason']})",
        "evidence": [{"kind": "diff", "ref": f"data/diffs/{diff_id}.json"}],
    }


def chain_events(
    index_data: dict[str, Any], snapshot_id: str, branch_id: str,
    load_diff: Callable[[str, str], dict[str, Any] | None], make_diff_id: Callable[[str, str], str],
) -> list[dict[str, Any]]:
    """События окна цепочки; load_diff(parent, child) возвращает дельту или None, если снимков нет на диске."""
    ids = chain_ids(index_data, snapshot_id)
    events: list[dict[str, Any]] = []
    for parent, child in zip(ids, ids[1:]):
        diff = load_diff(parent, child)
        if not diff or diff.get("valid") is False:
            continue
        entry = index_data["snapshots"][child]
        events += events_from_diff(diff, child, make_diff_id(parent, child), branch_id,
                                   entry.get("registered_at", ""), entry.get("game_time"))
    return events
