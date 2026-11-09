"""Реестр снимков kenshi-index/2: одна линия на кампанию, родитель — голова кампании."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from tools.io_utils import canonical, read_json

ROOT = Path(__file__).resolve().parents[1]
INDEX_FORMAT = "kenshi-index/2"
LEGACY_INDEX_FORMAT = "kenshi-index/1"
DEFAULT_INDEX_PATH = ROOT / "data/index.json"
ENTRY_FIELDS = ("snapshot_id", "parent_snapshot_id", "campaign_id", "game_time", "world_ref_id",
                "source_kind", "source_sha256", "registered_at")

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False


class CampaignIndexError(RuntimeError):
    """Отказ операций с индексом снимков."""


# Алиас для совместимости
IndexError = CampaignIndexError


class IndexLock:
    """Файловая блокировка для защиты от гонок при записи data/index.json."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fd: int | None = None

    def __enter__(self) -> IndexLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        if HAS_MSVCRT:
            try:
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            except (OSError, IOError) as error:
                os.close(self.fd)
                self.fd = None
                raise CampaignIndexError(f"Индекс заблокирован другим процессом: {error}") from error
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.fd is not None:
            try:
                if HAS_MSVCRT:
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self.fd)
                self.fd = None
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock_for(target: Path) -> IndexLock:
    return IndexLock(Path(tempfile.gettempdir()) / f"kenshi_{target.name}.lock")


def empty_index() -> dict[str, Any]:
    """Пустой индекс kenshi-index/2."""
    return {"format": INDEX_FORMAT, "updated_at": _now(), "campaigns": {}, "snapshots": {}}


def load_index(index_path: Path | None = None) -> dict[str, Any]:
    """Чтение индекса с диска. При отсутствии возвращается пустой шаблон."""
    target = index_path or DEFAULT_INDEX_PATH
    if not target.is_file():
        return empty_index()
    try:
        data = read_json(target.read_bytes())
    except Exception as err:
        raise CampaignIndexError(f"Ошибка чтения файла индекса {target}: {err}") from err
    if data.get("format") == LEGACY_INDEX_FORMAT:
        raise CampaignIndexError(
            f"Индекс {target} в формате {LEGACY_INDEX_FORMAT}: выполните python -m tools.campaign index migrate --apply")
    if data.get("format") != INDEX_FORMAT:
        raise CampaignIndexError(f"Некорректный формат индекса: {data.get('format')} (ожидался {INDEX_FORMAT})")
    return data


def save_index(index_data: dict[str, Any], index_path: Path | None = None, with_lock: bool = True) -> Path:
    """Атомарная запись индекса через staging-файл и replace на том же томе под блокировкой."""
    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    def _do_write() -> Path:
        staging_file = target.parent / f".tmp-{os.getpid()}-{target.name}"
        with open(staging_file, "wb") as f:
            f.write(canonical(index_data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging_file, target)
        return target

    if with_lock:
        with _lock_for(target):
            return _do_write()
    return _do_write()


def game_minutes(game_time: dict[str, Any] | None) -> int | None:
    """Игровое время записи индекса `{day, time}` в минутах; неполное время даёт None."""
    if not isinstance(game_time, dict) or game_time.get("day") is None:
        return None
    hours, _, minutes = str(game_time.get("time") or "").partition(":")
    try:
        return int(game_time["day"]) * 1440 + int(hours) * 60 + int(minutes or 0)
    except ValueError:
        return None


def campaign_head(index_data: dict[str, Any], campaign_id: str) -> str | None:
    return ((index_data.get("campaigns") or {}).get(campaign_id) or {}).get("head_snapshot_id")


def campaign_chain(
    index_data: dict[str, Any], snapshot_id: str, window: int | None = None, *, strict: bool = False,
) -> list[str]:
    """Цепочка от самого старого снимка к snapshot_id без снимков, отменённых перезагрузкой.

    Снимок отменён, если его игровое время больше времени более позднего снимка цепочки. Обход останавливается
    на границе кампании. window ограничивает число переходов (window + 1 снимок). strict требует, чтобы
    цепочка доходила до корня без родителя: оборванная связь или цикл — ошибка.
    """
    snapshots = index_data.get("snapshots", {})
    campaign = (snapshots.get(snapshot_id) or {}).get("campaign_id")
    kept: list[str] = []
    seen: set[str] = set()
    cutoff: int | None = None
    current: str | None = snapshot_id
    while current is not None:
        if current in seen:
            if strict:
                raise CampaignIndexError("Цепочка снимков в индексе содержит цикл")
            break
        entry = snapshots.get(current)
        if entry is None:
            if strict and current != snapshot_id:
                raise CampaignIndexError("Цепочка снимков в индексе оборвана")
            if current == snapshot_id:
                kept.append(current)
            break
        if current != snapshot_id and entry.get("campaign_id") != campaign:
            break
        seen.add(current)
        minutes = game_minutes(entry.get("game_time"))
        if minutes is None or cutoff is None or minutes <= cutoff:
            kept.append(current)
            if minutes is not None:
                cutoff = minutes
        if window is not None and len(kept) > window:
            break
        current = entry.get("parent_snapshot_id")
    return list(reversed(kept))


def reload_of(index_data: dict[str, Any], snapshot_id: str) -> dict[str, Any] | None:
    """Перезагрузка: игровое время снимка меньше времени родителя. Возвращает родителя и его время."""
    snapshots = index_data.get("snapshots", {})
    entry = snapshots.get(snapshot_id) or {}
    parent_id = entry.get("parent_snapshot_id")
    parent = snapshots.get(parent_id) if parent_id else None
    if parent is None:
        return None
    before, after = game_minutes(parent.get("game_time")), game_minutes(entry.get("game_time"))
    if before is None or after is None or after >= before:
        return None
    return {"from_snapshot": parent_id, "from_game_time": parent["game_time"]}


def check_ancestry(index_data: dict[str, Any], ancestor_id: str, descendant_id: str) -> bool:
    """Проверка, является ли ancestor_id предком descendant_id в графе родительства индекса."""
    if ancestor_id == descendant_id:
        return True
    snapshots = index_data.get("snapshots", {})
    curr: str | None = descendant_id
    visited: set[str] = set()
    while curr and curr not in visited:
        visited.add(curr)
        snap = snapshots.get(curr)
        if not snap:
            break
        parent = snap.get("parent_snapshot_id")
        if parent == ancestor_id:
            return True
        curr = parent
    return False


def check_chain_compatibility(index_data: dict[str, Any], prev_id: str, cur_id: str) -> tuple[bool, str | None]:
    """Снимки сравнимы, если prev_id — предок cur_id в цепочке индекса; иначе (False, 'not_in_chain')."""
    if check_ancestry(index_data, ancestor_id=prev_id, descendant_id=cur_id):
        return True, None
    return False, "not_in_chain"


def check_time_regression(prev_time: dict[str, Any] | None, cur_time: dict[str, Any] | None) -> bool:
    """Проверка регрессии игрового времени (to < from)."""
    before, after = game_minutes(prev_time), game_minutes(cur_time)
    return before is not None and after is not None and after < before


def _game_time(game_time: dict[str, Any] | None) -> dict[str, Any] | None:
    if not game_time or not isinstance(game_time, dict):
        return None
    return {"day": game_time.get("day"),
            "time": str(game_time.get("time")) if game_time.get("time") is not None else None}


def register_snapshot(
    snapshot_id: str,
    campaign_id: str,
    game_time: dict[str, Any] | None = None,
    world_ref_id: str | None = None,
    source_kind: str = "save",
    source_sha256: str = "",
    registered_at: str | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Регистрация снимка: родитель — голова кампании, снимок становится новой головой.

    Повторная регистрация существующего снимка той же кампании запись не меняет и переводит голову на него.
    """
    if not snapshot_id or not str(snapshot_id).strip():
        raise CampaignIndexError("snapshot_id обязан быть непустой строкой")
    if not campaign_id or not str(campaign_id).strip():
        raise CampaignIndexError("campaign_id обязан быть непустой строкой")

    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    with _lock_for(target):
        index_data = load_index(target)
        snapshots = index_data.setdefault("snapshots", {})
        campaigns = index_data.setdefault("campaigns", {})
        head = campaign_head(index_data, campaign_id)

        if snapshot_id in snapshots:
            existing = snapshots[snapshot_id]
            if (existing.get("campaign_id") != campaign_id or existing.get("source_kind") != source_kind
                    or existing.get("source_sha256") != source_sha256):
                raise CampaignIndexError(
                    f"Конфликт регистрационных данных для существующего снимка {snapshot_id}: "
                    f"кампания {existing.get('campaign_id')} -> {campaign_id}, sha {existing.get('source_sha256')} -> {source_sha256}")
            if head != snapshot_id:
                campaigns[campaign_id] = {"head_snapshot_id": snapshot_id}
                index_data["updated_at"] = _now()
                save_index(index_data, target, with_lock=False)
            return existing

        if head is not None and head not in snapshots:
            raise CampaignIndexError(f"Голова кампании {campaign_id} отсутствует в индексе: {head}")
        entry = {
            "snapshot_id": snapshot_id,
            "parent_snapshot_id": head,
            "campaign_id": campaign_id,
            "game_time": _game_time(game_time),
            "world_ref_id": world_ref_id,
            "source_kind": source_kind,
            "source_sha256": source_sha256,
            "registered_at": registered_at or _now(),
        }
        snapshots[snapshot_id] = entry
        campaigns[campaign_id] = {"head_snapshot_id": snapshot_id}
        index_data["updated_at"] = _now()
        save_index(index_data, target, with_lock=False)
        return entry


def _validated(index_data: dict[str, Any]) -> dict[str, Any]:
    from tools.validation import validate_index

    validate_index(index_data)
    return index_data


def migrate_legacy(legacy: dict[str, Any]) -> dict[str, Any]:
    """kenshi-index/1 -> kenshi-index/2: записи и связи сохраняются, ветки удаляются.

    Голова кампании — голова ветки, содержащей последний по registered_at снимок кампании, если эта голова
    относится к той же кампании; иначе сам последний снимок.
    """
    if legacy.get("format") != LEGACY_INDEX_FORMAT:
        raise CampaignIndexError(f"Ожидался {LEGACY_INDEX_FORMAT}, получено {legacy.get('format')}")
    old = legacy.get("snapshots", {})
    snapshots: dict[str, Any] = {}
    for sid, entry in sorted(old.items()):
        if not entry.get("campaign_id"):
            raise CampaignIndexError(f"Снимок {sid} без campaign_id: кампанию нельзя вывести")
        snapshots[sid] = {field: entry.get(field) for field in ENTRY_FIELDS}
    campaigns: dict[str, Any] = {}
    for campaign in sorted({entry["campaign_id"] for entry in snapshots.values()}):
        members = [sid for sid, entry in old.items() if entry.get("campaign_id") == campaign]
        latest = max(members, key=lambda sid: (old[sid].get("registered_at") or "", sid))
        head = ((legacy.get("branches") or {}).get(old[latest].get("branch_id")) or {}).get("head_snapshot_id")
        if head not in snapshots or snapshots[head]["campaign_id"] != campaign:
            head = latest
        campaigns[campaign] = {"head_snapshot_id": head}
    return _validated({"format": INDEX_FORMAT, "updated_at": legacy.get("updated_at") or _now(),
                       "campaigns": campaigns, "snapshots": snapshots})


def migrate_index(index_path: Path | None = None, apply: bool = False) -> dict[str, Any]:
    """План или выполнение миграции файла индекса с резервной копией; повтор на /2 ничего не меняет."""
    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    if not target.is_file():
        raise CampaignIndexError(f"Файл индекса не найден: {target}")
    with _lock_for(target):
        raw = target.read_bytes()
        data = read_json(raw)
        if data.get("format") == INDEX_FORMAT:
            _validated(data)
            return {"status": "unchanged", "format": INDEX_FORMAT, "snapshots": len(data["snapshots"])}
        migrated = migrate_legacy(data)
        report = {
            "status": "planned",
            "from": LEGACY_INDEX_FORMAT, "to": INDEX_FORMAT,
            "snapshots": len(migrated["snapshots"]),
            "branches_removed": sorted((data.get("branches") or {})),
            "campaigns": {cid: {"head_snapshot_id": c["head_snapshot_id"],
                                "game_time": migrated["snapshots"][c["head_snapshot_id"]]["game_time"],
                                "snapshots": sum(1 for e in migrated["snapshots"].values() if e["campaign_id"] == cid)}
                          for cid, c in migrated["campaigns"].items()},
        }
        if not apply:
            return report
        backup = target.with_name(f"{target.name}.v1-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak")
        shutil.copyfile(target, backup)
        if backup.read_bytes() != raw:
            raise CampaignIndexError("Резервная копия индекса не совпадает с исходником")
        save_index(migrated, target, with_lock=False)
        _validated(read_json(target.read_bytes()))
        return {**report, "status": "migrated", "backup": backup.name}


def rebuild_index(snapshots_dir: Path, index_path: Path | None = None) -> dict[str, Any]:
    """Восстановление записей индекса из data/snapshots/*/provenance.json.

    Связи и кампании берутся из существующего индекса (/2 или /1), иначе из provenance kenshi-snapshot/1.
    Снимки kenshi-snapshot/2 вне существующего индекса не содержат кампании и связи: они пропускаются
    и перечисляются в отчёте. Головы кампаний сохраняются из существующего индекса, если снимок на месте.
    """
    snapshots_dir = snapshots_dir.resolve()
    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    if not snapshots_dir.is_dir():
        raise CampaignIndexError(f"Каталог снимков {snapshots_dir} не найден")

    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            existing = read_json(target.read_bytes())
        except Exception:
            existing = {}
    if existing.get("format") == LEGACY_INDEX_FORMAT:
        existing = migrate_legacy(existing)
    known = existing.get("snapshots", {}) if existing.get("format") == INDEX_FORMAT else {}

    snapshots: dict[str, Any] = {}
    skipped: list[str] = []
    for snap_dir in sorted(snapshots_dir.iterdir()):
        prov_file = snap_dir / "provenance.json"
        if not snap_dir.is_dir() or snap_dir.name.startswith(".") or not prov_file.is_file():
            continue
        prov = read_json(prov_file.read_bytes())
        sid = prov.get("snapshot_id") or snap_dir.name
        if sid in snapshots:
            raise CampaignIndexError(f"Дублирующийся snapshot_id при восстановлении: {sid}")
        prior = known.get(sid, {})
        campaign = prior.get("campaign_id") or prov.get("campaign_id")
        if not campaign:
            skipped.append(sid)
            continue
        snapshots[sid] = {
            "snapshot_id": sid,
            "parent_snapshot_id": prior["parent_snapshot_id"] if prior else prov.get("parent_snapshot_id"),
            "campaign_id": campaign,
            "game_time": _game_time(prov.get("game_time")),
            "world_ref_id": prior.get("world_ref_id") or prov.get("world_ref_id"),
            "source_kind": prov.get("source_kind", "save"),
            "source_sha256": prov.get("source_sha256", ""),
            "registered_at": prior.get("registered_at") or _now(),
        }
    for entry in snapshots.values():
        if entry["parent_snapshot_id"] is not None and entry["parent_snapshot_id"] not in snapshots:
            raise CampaignIndexError(
                f"Снимок {entry['snapshot_id']} ссылается на отсутствующего родителя {entry['parent_snapshot_id']} (сирота)")

    campaigns: dict[str, Any] = {}
    for campaign in sorted({entry["campaign_id"] for entry in snapshots.values()}):
        head = campaign_head(existing, campaign)
        if head not in snapshots:
            members = [sid for sid, entry in snapshots.items() if entry["campaign_id"] == campaign]
            head = max(members, key=lambda sid: (snapshots[sid]["registered_at"], sid))
        campaigns[campaign] = {"head_snapshot_id": head}

    rebuilt = _validated({"format": INDEX_FORMAT, "updated_at": _now(), "campaigns": campaigns, "snapshots": snapshots})
    save_index(rebuilt, target, with_lock=True)
    return {**rebuilt, "skipped_unindexed": skipped}


def find_snapshot_by_day(index_data: dict[str, Any], day: int, campaign_id: str | None = None) -> str | None:
    """Снимок цепочки головы кампании на игровой день (или ближайший)."""
    campaigns = index_data.get("campaigns", {})
    if campaign_id is None:
        if len(campaigns) != 1:
            return None
        campaign_id = next(iter(campaigns))
    head = campaign_head(index_data, campaign_id)
    if head is None:
        return None
    candidates = []
    for sid in campaign_chain(index_data, head):
        game_time = index_data["snapshots"][sid].get("game_time")
        if isinstance(game_time, dict) and game_time.get("day") is not None:
            candidates.append((game_time["day"], sid))
    if not candidates:
        return None
    exact = [sid for d, sid in candidates if d == day]
    if exact:
        return exact[-1]
    candidates.sort(key=lambda item: (abs(item[0] - day), -item[0]))
    return candidates[0][1]
