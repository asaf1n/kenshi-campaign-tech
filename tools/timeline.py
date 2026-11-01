"""Append-only журнал событий и отдельные авторские сессии кампании."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator

from tools.io_utils import canonical, linked, read_json
from tools.normalization import scrub
from tools.validation import validate_event, validate_index, validate_timeline, validate_timeline_id

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMELINE = ROOT / "timeline/events.jsonl"


@contextmanager
def _writer_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Отдельный постоянный inode: замена журнала не снимает блокировку writer.
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _decode(raw: bytes, *, projection: bool = False, validate_graph: bool = True) -> list[dict[str, Any]]:
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Повреждённая строка журнала: отсутствует завершающий перевод строки")
    events = []
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            event = read_json(line)
            validate_event(event)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Повреждённая строка журнала: {number}") from error
        events.append(event)
    if validate_graph:
        validate_timeline(events, projection=projection)
    return events


def read_timeline(path: Path = DEFAULT_TIMELINE, *, projection: bool = False) -> list[dict[str, Any]]:
    return _decode(path.read_bytes(), projection=projection) if path.exists() else []


def read_event_batch(path: Path) -> list[dict[str, Any]]:
    # Ссылки коррекций проверяются вместе с уже принятым журналом под writer-lock.
    return _decode(path.read_bytes(), validate_graph=False)


def _inside(root: Path, relative: str) -> Path:
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Путь выходит из рабочего каталога")
    if any(linked(p) for p in (target, *target.parents) if p != root.parent):
        raise ValueError("Ссылки в пути журнала, сессии или источника запрещены")
    return target


def _validate_session(session: dict[str, Any], index_data: dict[str, Any]) -> None:
    if set(session) != {"session_id", "snapshot_ids"}:
        raise ValueError("Сессия содержит session_id и snapshot_ids")
    validate_timeline_id(session["session_id"], "session_id")
    ids = session["snapshot_ids"]
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ValueError("Сессия требует непустой список уникальных snapshot_ids")
    for sid in ids:
        validate_timeline_id(sid, "snapshot_id")
        if sid == session["session_id"] or sid not in index_data["snapshots"]:
            raise ValueError("Сессия требует существующие снимки с отдельными ID")


def create_session(
    session_id: str, snapshot_ids: list[str], index_data: dict[str, Any],
    root: Path = ROOT, notes: str | None = None,
) -> dict[str, Any]:
    """Создать неизменяемую привязку сессии; заметки остаются авторским Markdown."""
    validate_index(index_data)
    session = {"session_id": session_id, "snapshot_ids": sorted(set(snapshot_ids))}
    _validate_session(session, index_data)
    destination = _inside(root, f"sessions/{session_id}")
    if notes is None:
        notes = (ROOT / "templates/session-notes.md").read_text(encoding="utf-8")
        notes = notes.replace("<session_id>", session_id).replace(
            "<snapshot_id_1>, <snapshot_id_2>, ...", ", ".join(session["snapshot_ids"]))
    with _writer_lock(_inside(root, "sessions/.writer.lock")):
        if destination.exists():
            if read_json((destination / "session.json").read_bytes()) != session:
                raise ValueError("Конфликт session_id")
            return session
        temporary = Path(tempfile.mkdtemp(prefix=".session-", dir=destination.parent))
        try:
            (temporary / "session.json").write_bytes(canonical(session))
            (temporary / "notes.md").write_text(notes, encoding="utf-8")
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return session


def load_sessions(root: Path, index_data: dict[str, Any]) -> dict[str, Any]:
    sessions = {}
    for path in sorted(_inside(root, "sessions").glob("*/session.json")):
        _inside(root, path.relative_to(root).as_posix())
        session = read_json(path.read_bytes())
        _validate_session(session, index_data)
        if session["session_id"] != path.parent.name:
            raise ValueError("ID сессии не совпадает с каталогом")
        sessions[session["session_id"]] = session
    return sessions


def _check_sources(event: dict[str, Any], root: Path, index_data: dict[str, Any]) -> None:
    for source in event["evidence"]:
        ref = source["ref"]
        if source["kind"] == "snapshot" and ref in index_data["snapshots"]:
            ref = f"data/snapshots/{ref}/current.json"
        target = _inside(root, ref)
        if source["kind"] == "snapshot":
            parts = ref.split("/")
            if (len(parts) != 4 or parts[:2] != ["data", "snapshots"]
                    or parts[-1] != "current.json" or parts[2] not in index_data["snapshots"]):
                raise ValueError("Источник snapshot должен ссылаться на известный снимок")
        elif source["kind"] == "diff":
            if not ref.startswith("data/diffs/") or len(ref.split("/")) != 3:
                raise ValueError("Источник diff должен ссылаться на известное сравнение")
            if target.is_file():
                comparison = read_json(target.read_bytes())
                if (comparison.get("format") != "kenshi-session-diff/5" or comparison.get("valid") is not True
                        or any((comparison.get(side) or {}).get("snapshot_id") not in index_data["snapshots"] for side in ("from", "to"))):
                    raise ValueError("Источник diff не подтверждает допустимое сравнение снимков")
        if event["status"] != "inferred" and not target.is_file():
            raise ValueError(f"Источник недоступен для подтверждённого события: {event['event_id']}")


def import_events(
    events: list[dict[str, Any]], path: Path = DEFAULT_TIMELINE, *,
    index_data: dict[str, Any] | None = None, root: Path | None = None,
) -> dict[str, int]:
    """Проверить весь пакет и атомарно добавить его байты без изменения префикса."""
    root = root or path.parent.parent
    path = _inside(root, path.resolve().relative_to(root.resolve()).as_posix())
    if index_data is None:
        from tools.index import load_index
        index_data = load_index(root / "data/index.json")
    validate_index(index_data)
    sessions = load_sessions(root, index_data)
    lock = _inside(root, path.with_suffix(".lock").relative_to(root).as_posix())
    with _writer_lock(lock):
        original = path.read_bytes() if path.exists() else b""
        existing = _decode(original)
        known = {e["event_id"]: e for e in existing}
        additions = []
        for event in events:
            validate_event(event, index_data)
            identity = event["event_id"]
            if identity in known:
                if known[identity] != event:
                    raise ValueError("Конфликт event_id")
                continue
            session = sessions.get(event["session_id"])
            if session is None:
                raise ValueError("Неизвестная сессия события")
            if event.get("snapshot_id") and event["snapshot_id"] not in session["snapshot_ids"]:
                raise ValueError("Снимок не связан с сессией события")
            _check_sources(event, root, index_data)
            known[identity] = event
            additions.append(event)
        validate_timeline(existing + additions, index_data)
        if additions:
            fd, name = tempfile.mkstemp(prefix=".events-", dir=path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(original)
                    stream.write(b"".join(canonical(e) for e in additions))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return {"added": len(additions), "unchanged": len(events) - len(additions)}


def append_event(event: dict[str, Any], path: Path = DEFAULT_TIMELINE, **kwargs: Any) -> dict[str, int]:
    return import_events([event], path, **kwargs)


def resolve_timeline(events: list[dict[str, Any]], *, include_inferred: bool = False) -> list[dict[str, Any]]:
    """Последнее доказанное утверждение цепочки; inferred не отменяет доказанный факт."""
    validate_timeline(events)
    corrections = {e["corrects_event_id"]: e for e in events if e["kind"] == "correction"}
    resolved = []
    for event in events:
        if event["kind"] == "correction":
            continue
        chosen = None
        cursor = event
        while cursor is not None:
            if include_inferred or cursor["status"] != "inferred":
                chosen = cursor
            cursor = corrections.get(cursor["event_id"])
        if chosen is not None:
            resolved.append(copy.deepcopy(chosen))
    return sorted(resolved, key=lambda e: (e.get("game_day") is None, e.get("game_day") or 0, e["event_id"]))


def filter_events_by_branch(
    events: list[dict[str, Any]], branch_id: str, index_data: dict[str, Any],
    sessions: dict[str, Any] | None = None, *, snapshot_id: str | None = None,
) -> list[dict[str, Any]]:
    validate_index(index_data)
    if branch_id not in index_data["branches"]:
        raise ValueError("Неизвестная ветка таймлайна")
    snapshots = index_data["snapshots"]
    head = snapshot_id or index_data["branches"][branch_id]["head_snapshot_id"]
    if head is not None and (head not in snapshots or snapshots[head]["branch_id"] != branch_id):
        raise ValueError("Снимок публикации не принадлежит ветке")
    ancestry = set()
    while head is not None:
        ancestry.add(head)
        head = snapshots[head]["parent_snapshot_id"]
    selected = []
    for event in events:
        sid = event.get("snapshot_id")
        linked_ids = (sessions or {}).get(event["session_id"], {}).get("snapshot_ids", [])
        if (sid is not None and sid in ancestry) or (sid is None and linked_ids and set(linked_ids) <= ancestry):
            selected.append(event)
    # Коррекция из выбранной ветки не может изменять утверждение другой ветки.
    known = {e["event_id"] for e in selected}
    while True:
        kept = [e for e in selected if e["kind"] != "correction" or e["corrects_event_id"] in known]
        if len(kept) == len(selected):
            return copy.deepcopy(kept)
        selected = kept
        known = {e["event_id"] for e in selected}


filter_by_branch = filter_events_by_branch


def project_timeline(
    events: list[dict[str, Any]], snapshot_id: str, diff_id: str,
) -> list[dict[str, Any]]:
    """Очистить действующие факты; локальные источники не выдаются за доступные в bundle."""
    projected = scrub(resolve_timeline(events))
    for event in projected:
        for source in event["evidence"]:
            if source["kind"] == "snapshot" and source["ref"] in {
                snapshot_id, f"data/snapshots/{snapshot_id}/current.json",
            }:
                source["ref"] = "current.json"
            elif source["kind"] == "diff" and source["ref"] == f"data/diffs/{diff_id}.json":
                source["ref"] = "changes.json"
            else:
                source["note"] = "Локальный источник; недоступен облачному читателю. " + source.get("note", "")
    validate_timeline(projected, projection=True)
    return projected
