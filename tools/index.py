"""Реестр снимков, отслеживание родительства и веток: kenshi-index/1."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from typing import Any

from tools.io_utils import canonical, read_json

ROOT = Path(__file__).resolve().parents[1]
INDEX_FORMAT = "kenshi-index/1"
DEFAULT_INDEX_PATH = ROOT / "data/index.json"

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False


class CampaignIndexError(RuntimeError):
    """Отказ операций с индексом снимков и веток."""


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


def empty_index(active_branch: str = "main") -> dict[str, Any]:
    """Создание пустой структуры индекса kenshi-index/1."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "format": INDEX_FORMAT,
        "updated_at": now_iso,
        "active_branch_id": active_branch,
        "branches": {
            active_branch: {
                "name": "Основная ветка" if active_branch == "main" else active_branch,
                "head_snapshot_id": None,
                "created_at": now_iso,
            }
        },
        "snapshots": {},
    }


def load_index(index_path: Path | None = None) -> dict[str, Any]:
    """Чтение индекса с диска. При отсутствии возвращается пустой шаблон."""
    target = index_path or DEFAULT_INDEX_PATH
    if not target.is_file():
        return empty_index()
    try:
        data = read_json(target.read_bytes())
    except Exception as err:
        raise CampaignIndexError(f"Ошибка чтения файла индекса {target}: {err}") from err

    if data.get("format") != INDEX_FORMAT:
        raise CampaignIndexError(f"Некорректный формат индекса: {data.get('format')} (ожидался {INDEX_FORMAT})")
    return data


def save_index(index_data: dict[str, Any], index_path: Path | None = None, with_lock: bool = True) -> Path:
    """Атомарная запись индекса через staging-файл и replace на том же томе под блокировкой."""
    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    lock_file = Path(tempfile.gettempdir()) / f"kenshi_{target.name}.lock"

    def _do_write() -> Path:
        staging_file = target.parent / f".tmp-{os.getpid()}-{target.name}"
        data_bytes = canonical(index_data)
        with open(staging_file, "wb") as f:
            f.write(data_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging_file, target)
        return target

    if with_lock:
        with IndexLock(lock_file):
            return _do_write()
    return _do_write()


def check_ancestry(index_data: dict[str, Any], ancestor_id: str, descendant_id: str) -> bool:
    """Проверка, является ли ancestor_id предком descendant_id в графе родительства."""
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


def check_branch_compatibility(index_data: dict[str, Any], prev_id: str, cur_id: str) -> tuple[bool, str | None]:
    """Проверка совместимости двух снимков для хронологического сравнения.

    Снимки совместимы тогда и только тогда, когда prev_id является прямым предком cur_id.
    Снимки из разных веток или параллельные ветки reload возвращают (False, 'different_branches').
    """
    if prev_id == cur_id:
        return True, None

    snapshots = index_data.get("snapshots", {})
    cur_snap = snapshots.get(cur_id)
    prev_snap = snapshots.get(prev_id)

    # Если cur_id явно заявляет prev_id как родителя
    if cur_snap and cur_snap.get("parent_snapshot_id") == prev_id:
        return True, None

    # Проверка транзитивного предка
    if check_ancestry(index_data, ancestor_id=prev_id, descendant_id=cur_id):
        return True, None

    # Иначе снимки принадлежат параллельным или разным веткам
    return False, "different_branches"


def check_time_regression(prev_time: dict[str, Any] | None, cur_time: dict[str, Any] | None) -> bool:
    """Проверка регрессии игрового времени (to < from)."""
    if not prev_time or not cur_time:
        return False

    prev_day = prev_time.get("day")
    cur_day = cur_time.get("day")
    if prev_day is not None and cur_day is not None:
        if cur_day < prev_day:
            return True
        if cur_day == prev_day:
            pt = str(prev_time.get("time") or "")
            ct = str(cur_time.get("time") or "")
            if pt and ct and ct < pt:
                return True
    return False


def register_snapshot(
    snapshot_id: str,
    parent_snapshot_id: str | None = None,
    branch_id: str | None = None,
    campaign_id: str | None = None,
    game_time: dict[str, Any] | None = None,
    world_ref_id: str | None = None,
    source_kind: str = "save",
    source_sha256: str = "",
    registered_at: str | None = None,
    index_path: Path | None = None,
    update_branch_head: bool = True,
) -> dict[str, Any]:
    """Регистрация снимка в реестре data/index.json с защитой от циклов, дубликатов и сирот."""
    if not snapshot_id or not str(snapshot_id).strip():
        raise CampaignIndexError("snapshot_id обязан быть непустой строкой")

    if parent_snapshot_id is not None and parent_snapshot_id == snapshot_id:
        raise CampaignIndexError(f"Снимок {snapshot_id} не может быть собственным родителем (цикл)")

    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    lock_file = Path(tempfile.gettempdir()) / f"kenshi_{target.name}.lock"

    with IndexLock(lock_file):
        index_data = load_index(target)
        snapshots = index_data.setdefault("snapshots", {})
        branches = index_data.setdefault("branches", {})

        # Проверка существующей записи (идемпотентность или конфликт)
        if snapshot_id in snapshots:
            existing = snapshots[snapshot_id]
            is_same = (
                existing.get("parent_snapshot_id") == parent_snapshot_id
                and existing.get("source_kind") == source_kind
                and existing.get("source_sha256") == source_sha256
            )
            if is_same:
                return existing
            raise CampaignIndexError(
                f"Конфликт регистрационных данных для существующего снимка {snapshot_id}: "
                f"существующий={existing}, новый parent={parent_snapshot_id}, sha={source_sha256}"
            )

        # Проверка родителя (защита от сирот и циклов)
        if parent_snapshot_id is not None:
            if parent_snapshot_id not in snapshots:
                raise CampaignIndexError(
                    f"Родительский снимок {parent_snapshot_id} отсутствует в индексе (сирота)"
                )

            # Проверка циклов (обход вверх от parent_snapshot_id)
            curr = parent_snapshot_id
            visited = {snapshot_id}
            while curr:
                if curr in visited:
                    raise CampaignIndexError(f"Обнаружен цикл в графе родительства: {snapshot_id} -> {curr}")
                visited.add(curr)
                parent_entry = snapshots.get(curr)
                if not parent_entry:
                    break
                curr = parent_entry.get("parent_snapshot_id")

        # Определение ветки
        eff_branch = branch_id or index_data.get("active_branch_id") or "main"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if eff_branch not in branches:
            branches[eff_branch] = {
                "name": eff_branch,
                "head_snapshot_id": None,
                "created_at": now_iso,
            }

        parsed_gt: dict[str, Any] | None = None
        if game_time:
            parsed_gt = {
                "day": game_time.get("day"),
                "time": str(game_time.get("time")) if game_time.get("time") is not None else None,
            }

        entry = {
            "snapshot_id": snapshot_id,
            "parent_snapshot_id": parent_snapshot_id,
            "branch_id": eff_branch,
            "campaign_id": campaign_id,
            "game_time": parsed_gt,
            "world_ref_id": world_ref_id,
            "source_kind": source_kind,
            "source_sha256": source_sha256,
            "registered_at": registered_at or now_iso,
        }

        snapshots[snapshot_id] = entry

        if update_branch_head:
            branches[eff_branch]["head_snapshot_id"] = snapshot_id

        index_data["updated_at"] = now_iso
        save_index(index_data, target, with_lock=False)
        return entry


def rebuild_index(snapshots_dir: Path, index_path: Path | None = None) -> dict[str, Any]:
    """Восстановление реестра из существующих data/snapshots/*/provenance.json.

    Не назначает текущий опубликованный снимок автоматически!
    Проверяет отсутствие циклов, дубликатов и сирот.
    """
    snapshots_dir = snapshots_dir.resolve()
    target = (index_path or DEFAULT_INDEX_PATH).resolve()

    if not snapshots_dir.is_dir():
        raise CampaignIndexError(f"Каталог снимков {snapshots_dir} не найден")

    # Сбор всех провенансов
    raw_snapshots: dict[str, dict[str, Any]] = {}
    for snap_dir in sorted(snapshots_dir.iterdir()):
        if snap_dir.is_dir() and not snap_dir.name.startswith("."):
            prov_file = snap_dir / "provenance.json"
            if prov_file.is_file():
                prov = read_json(prov_file.read_bytes())
                sid = prov.get("snapshot_id") or snap_dir.name
                if sid in raw_snapshots:
                    raise CampaignIndexError(f"Дублирующийся snapshot_id при восстановлении: {sid}")
                raw_snapshots[sid] = prov

    # Проверка целостности связей и отсутствия циклов
    for sid, prov in raw_snapshots.items():
        parent_id = prov.get("parent_snapshot_id")
        if parent_id is not None:
            if parent_id == sid:
                raise CampaignIndexError(f"Снимок {sid} ссылается сам на себя (цикл)")
            if parent_id not in raw_snapshots:
                raise CampaignIndexError(f"Снимок {sid} ссылается на отсутствующего родителя {parent_id} (сирота)")

            curr = parent_id
            visited = {sid}
            while curr:
                if curr in visited:
                    raise CampaignIndexError(f"Обнаружен цикл в графе родительства: {sid} -> {curr}")
                visited.add(curr)
                p_prov = raw_snapshots.get(curr)
                if not p_prov:
                    break
                curr = p_prov.get("parent_snapshot_id")

    # Чтение существующего индекса для сохранения метаданных веток, если возможно
    existing_index: dict[str, Any] = {}
    if target.is_file():
        try:
            existing_index = read_json(target.read_bytes())
        except Exception:
            existing_index = {}

    active_branch = existing_index.get("active_branch_id") or "main"
    existing_branches = existing_index.get("branches", {})

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    branches: dict[str, Any] = {}
    if active_branch in existing_branches:
        branches[active_branch] = dict(existing_branches[active_branch])
    else:
        branches[active_branch] = {
            "name": "Основная ветка" if active_branch == "main" else active_branch,
            "head_snapshot_id": None,
            "created_at": now_iso,
        }

    # Построение записей снимков
    snapshots: dict[str, Any] = {}
    for sid, prov in raw_snapshots.items():
        b_id = prov.get("branch_id") or active_branch
        if b_id not in branches:
            if b_id in existing_branches:
                branches[b_id] = dict(existing_branches[b_id])
            else:
                branches[b_id] = {
                    "name": b_id,
                    "head_snapshot_id": None,
                    "created_at": now_iso,
                }

        gt = prov.get("game_time")
        parsed_gt = None
        if gt and isinstance(gt, dict):
            parsed_gt = {
                "day": gt.get("day"),
                "time": str(gt.get("time")) if gt.get("time") is not None else None,
            }

        snapshots[sid] = {
            "snapshot_id": sid,
            "parent_snapshot_id": prov.get("parent_snapshot_id"),
            "branch_id": b_id,
            "campaign_id": prov.get("campaign_id"),
            "game_time": parsed_gt,
            "world_ref_id": prov.get("world_ref_id"),
            "source_kind": prov.get("source_kind", "save"),
            "source_sha256": prov.get("source_sha256", ""),
            "registered_at": prov.get("registered_at") or now_iso,
        }

    # Обновление головных снимков веток по топологическому порядку / времени
    for sid, entry in snapshots.items():
        bid = entry["branch_id"]
        # Снимок является кандидатом на head, если у него нет детей в той же ветке
        is_leaf = True
        for other_sid, other_entry in snapshots.items():
            if other_entry.get("parent_snapshot_id") == sid and other_entry.get("branch_id") == bid:
                is_leaf = False
                break
        if is_leaf:
            branches[bid]["head_snapshot_id"] = sid

    rebuilt_data = {
        "format": INDEX_FORMAT,
        "updated_at": now_iso,
        "active_branch_id": active_branch,
        "branches": branches,
        "snapshots": snapshots,
    }

    save_index(rebuilt_data, target, with_lock=True)
    return rebuilt_data


def switch_branch(
    branch_id: str,
    create: bool = False,
    name: str | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Переключение активной ветки в data/index.json."""
    target = (index_path or DEFAULT_INDEX_PATH).resolve()
    lock_file = Path(tempfile.gettempdir()) / f"kenshi_{target.name}.lock"

    with IndexLock(lock_file):
        index_data = load_index(target)
        branches = index_data.setdefault("branches", {})

        if branch_id not in branches:
            if not create:
                raise CampaignIndexError(f"Ветка '{branch_id}' не найдена в индексе")
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            branches[branch_id] = {
                "name": name or branch_id,
                "head_snapshot_id": None,
                "created_at": now_iso,
            }

        index_data["active_branch_id"] = branch_id
        index_data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_index(index_data, target, with_lock=False)
        return index_data


def get_branch_tree(index_data: dict[str, Any]) -> dict[str, Any]:
    """Формирование древовидной структуры снимков и веток."""
    snapshots = index_data.get("snapshots", {})
    branches = index_data.get("branches", {})
    active_branch = index_data.get("active_branch_id")

    children_map: dict[str | None, list[str]] = {}
    for sid, entry in snapshots.items():
        pid = entry.get("parent_snapshot_id")
        children_map.setdefault(pid, []).append(sid)

    def _build_subtree(node_id: str) -> dict[str, Any]:
        node = snapshots.get(node_id, {})
        kids = children_map.get(node_id, [])
        return {
            "snapshot_id": node_id,
            "branch_id": node.get("branch_id"),
            "game_time": node.get("game_time"),
            "children": [_build_subtree(kid) for kid in kids],
        }

    roots = children_map.get(None, [])
    tree_roots = [_build_subtree(r) for r in roots]

    return {
        "active_branch_id": active_branch,
        "branches": branches,
        "roots": tree_roots,
    }


def find_snapshot_by_day(index_data: dict[str, Any], day: int, branch_id: str | None = None) -> str | None:
    """Найти снимок на ветке по игровому дню (или ближайший)."""
    target_branch = branch_id or index_data.get("active_branch_id")
    candidates = []
    for sid, entry in index_data.get("snapshots", {}).items():
        if target_branch and entry.get("branch_id") != target_branch:
            continue
        gt = entry.get("game_time")
        if isinstance(gt, dict) and gt.get("day") is not None:
            candidates.append((gt["day"], sid))

    if not candidates:
        return None

    exact = [sid for d, sid in candidates if d == day]
    if exact:
        return exact[0]

    candidates.sort(key=lambda item: (abs(item[0] - day), -item[0]))
    return candidates[0][1]
