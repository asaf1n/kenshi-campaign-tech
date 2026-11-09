"""Накопительный таймлайн по цепочке снимков кампании и событие перезагрузки.

События восстанавливаются детерминированно из дельт между последовательными снимками цепочки (родитель ->
потомок) и не требуют отдельного журнала. Статус `observed`: факты вычислены по данным сохранений. В выгрузку
попадает окно из последних WINDOW_EDGES переходов; полная цепочка остаётся в `data/` конвертера. Снимки,
отменённые перезагрузкой, в цепочку не входят, переход через перезагрузку дельты не даёт.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from tools.index import campaign_chain

WINDOW_EDGES = 20
NAMES_IN_SUMMARY = 6
AUTO_SESSION_ID = "auto-chain"


def chain_ids(index_data: dict[str, Any], snapshot_id: str, window: int = WINDOW_EDGES) -> list[str]:
    """Идентификаторы от самого старого снимка окна к текущему без снимков, отменённых перезагрузкой."""
    return campaign_chain(index_data, snapshot_id, window)


def chain_edges(index_data: dict[str, Any], snapshot_id: str, window: int = WINDOW_EDGES) -> list[tuple[str, str]]:
    """Пары (родитель, потомок) окна цепочки; пара через перезагрузку (родитель отменён) исключается."""
    ids = chain_ids(index_data, snapshot_id, window)
    snapshots = index_data.get("snapshots", {})
    return [(parent, child) for parent, child in zip(ids, ids[1:])
            if (snapshots.get(child) or {}).get("parent_snapshot_id") == parent]


def _names(entries: list[dict[str, Any]]) -> str:
    names = [str(entry.get("name") or entry.get("id")) for entry in entries]
    tail = f" и ещё {len(names) - NAMES_IN_SUMMARY}" if len(names) > NAMES_IN_SUMMARY else ""
    return ", ".join(names[:NAMES_IN_SUMMARY]) + tail


def game_clock(game_time: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """День и `Day N HH:MM` из записи индекса снимка; неполное время даёт None (поле события опускается)."""
    if not game_time or not isinstance(game_time.get("day"), int):
        return None, None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(game_time.get("time") or ""))
    day = game_time["day"]
    return day, (f"Day {day} {int(match.group(1)):02d}:{match.group(2)}" if match else None)


def significant_changes(diff: dict[str, Any]) -> list[tuple[str, str]]:
    """Значимые изменения дельты парами (kind, summary); новые виды дописываются в конец: порядковые ID событий устойчивы."""
    world = diff.get("world", {})
    roster = diff.get("roster") or {}
    found: list[tuple[str, str]] = []
    if roster.get("joined"):
        found.append(("squad_change", f"В отряд вступили: {_names(roster['joined'])}"))
    if roster.get("left"):
        found.append(("squad_change", f"Покинули отряд: {_names(roster['left'])}"))
    if roster.get("squad_changed"):
        found.append(("squad_change", f"Сменили отряд ({len(roster['squad_changed'])}): {_names(roster['squad_changed'])}"))
    if diff.get("prosthetics"):
        limbs = "; ".join(
            f"{entry.get('name') or entry.get('id')}: " + ", ".join(
                f"{slot} {value.get('from')} -> {value.get('to')}" for slot, value in sorted((entry.get("changes") or {}).items()))
            for entry in diff["prosthetics"])
        found.append(("state_delta", f"Изменены конечности и протезы: {limbs}"))
    money = world.get("money") or {}
    if money.get("delta"):
        found.append(("state_delta", f"Казна: {money.get('from')} -> {money.get('to')} ({money['delta']:+})"))
    statuses = roster.get("status_changed") or []
    if statuses:
        found.append(("state_delta", "Смена статуса: " + ", ".join(
            f"{entry.get('name') or entry.get('id')} {entry.get('field')}={entry.get('to')}" for entry in statuses)))
    diplomacy = diff.get("diplomacy") or []
    if diplomacy:
        found.append(("state_delta", f"Изменены отношения с фракциями ({len(diplomacy)}): " + ", ".join(
            str(entry.get("faction_name") or entry.get("faction_sid")) for entry in diplomacy[:NAMES_IN_SUMMARY])))
    world_state = diff.get("world_state") or {}
    world_changes = len(world_state.get("unique_npcs") or []) + len(world_state.get("town_overrides") or [])
    if world_changes:
        found.append(("state_delta", f"Изменения World States: {world_changes}"))
    return found


def events_from_diff(
    diff: dict[str, Any], child_id: str, diff_id: str, recorded_at: str,
    game_time: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Значимые изменения дельты в событиях kenshi-event/1."""
    day, clock = game_clock(game_time)
    found = significant_changes(diff)

    prefix = child_id.removeprefix("sha256-")[:16]
    events = []
    for ordinal, (kind, summary) in enumerate(found, 1):
        event = {
            "format": "kenshi-event/1", "event_id": f"auto-{prefix}-{ordinal:02d}", "snapshot_id": child_id,
            "session_id": AUTO_SESSION_ID, "recorded_at": recorded_at, "kind": kind, "status": "observed",
            "summary": summary, "evidence": [{"kind": "diff", "ref": f"data/diffs/{diff_id}.json"}],
        }
        if day is not None:
            event["game_day"] = day
        if clock:
            event["game_time"] = clock
        events.append(event)
    return events


def reload_event(
    index_data: dict[str, Any], snapshot_id: str, reload: dict[str, Any], diff_id: str,
) -> dict[str, Any]:
    """Перезагрузка: игровое время снимка меньше времени родителя (tools.index.reload_of)."""
    entry = index_data["snapshots"][snapshot_id]
    day, clock = game_clock(entry.get("game_time"))
    _, before = game_clock(reload["from_game_time"])
    prefix = snapshot_id.removeprefix("sha256-")[:16]
    event = {
        "format": "kenshi-event/1", "event_id": f"auto-{prefix}-reload", "snapshot_id": snapshot_id,
        "session_id": AUTO_SESSION_ID, "recorded_at": entry.get("registered_at", ""), "kind": "reload",
        "status": "observed",
        "summary": f"Перезагрузка: игровое время {before or 'неизвестно'} -> {clock or 'неизвестно'}; "
                   f"снимки позже {clock or 'точки загрузки'} исключены из цепочки",
        "evidence": [{"kind": "diff", "ref": f"data/diffs/{diff_id}.json"}],
    }
    if day is not None:
        event["game_day"] = day
    if clock:
        event["game_time"] = clock
    return event


def chain_events(
    index_data: dict[str, Any], snapshot_id: str,
    load_diff: Callable[[str, str], dict[str, Any] | None], make_diff_id: Callable[[str, str], str],
) -> list[dict[str, Any]]:
    """События окна цепочки; load_diff(parent, child) возвращает дельту или None, если снимков нет на диске."""
    events: list[dict[str, Any]] = []
    for parent, child in chain_edges(index_data, snapshot_id):
        diff = load_diff(parent, child)
        if not diff or diff.get("valid") is False:
            continue
        entry = index_data["snapshots"][child]
        events += events_from_diff(diff, child, make_diff_id(parent, child),
                                   entry.get("registered_at", ""), entry.get("game_time"))
    return events
