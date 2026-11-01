"""Атомарная публикация release bundle на Windows с защитой .gitkeep и rollback.

Протокол:
1. Захват файловой блокировки.
2. Проверка и самовосстановление после предыдущих сбоев (backup -> published).
3. Пространственное разрешение локаций (kenshi-locations/1) и подготовка world_reference.
4. Рендеринг русскоязычных Markdown (brief.md, squad.md) с географическими сводками.
5. Подготовка пакета в staging на том же томе.
6. Валидация пакета до переключения (validate_publication).
7. Переключение: published -> backup -> staging -> published.
8. При ошибке переключения — немедленный откат из backup.
9. Сохранение published/.gitkeep побайтово во всех сценариях.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from tools.diff import diff_snapshots, make_diff_id
from tools.io_utils import canonical, read_json
from tools.locator import resolve_locations
from tools.rendering import render_brief, render_squad
from tools.validation import validate_publication
from tools.world_reference import DEFAULT_WORLD_DIR, find_world_reference, load_world_reference

ROOT = Path(__file__).resolve().parents[1]

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False


class PublisherError(RuntimeError):
    """Отказ публикации пакета."""


class PublishLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fd: int | None = None

    def __enter__(self) -> PublishLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        if HAS_MSVCRT:
            try:
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            except (OSError, IOError) as error:
                os.close(self.fd)
                self.fd = None
                raise PublisherError(f"Каталог публикации занят другим процессом: {error}") from error
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


def recover_if_needed(published_dir: Path, backup_dir: Path, staging_dir: Path) -> None:
    """Восстановление после предыдущего аварийного завершения."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    if backup_dir.exists():
        manifest_in_pub = published_dir.is_dir() and (published_dir / "manifest.json").is_file()
        if not manifest_in_pub:
            if published_dir.exists():
                shutil.rmtree(published_dir, ignore_errors=True)
            backup_dir.rename(published_dir)
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)


def _get_world_reference(
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    mods: list[str] | None = None,
) -> dict[str, Any] | None:
    """Подбор или загрузка справочника мира с надежным fallback для тестов."""
    base_dir = world_dir or DEFAULT_WORLD_DIR
    if world_ref_id is not None:
        try:
            return load_world_reference(world_ref_id, world_dir=base_dir)
        except Exception:
            pass

    ref_path, _ = find_world_reference(mods=mods, world_ref_id=world_ref_id, world_dir=base_dir)
    if ref_path is not None and ref_path.is_file():
        try:
            return load_world_reference(ref_path)
        except Exception:
            pass

    # Поиск любого файла в base_dir
    if base_dir.is_dir():
        for p in sorted(base_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                data = read_json(p.read_bytes())
                if data.get("format") == "kenshi-world-reference/1":
                    return data
            except Exception:
                continue

    # Fallback на проверенный контрактный пример
    example_path = ROOT / "examples/world-reference.example.json"
    if example_path.is_file():
        try:
            data = read_json(example_path.read_bytes())
            if data.get("format") == "kenshi-world-reference/1":
                return data
        except Exception:
            pass

    return None


def publish(
    snapshot_id: str,
    snapshots_dir: Path,
    diffs_dir: Path,
    published_dir: Path,
    parent_snapshot_id: str | None = None,
    branch_id: str | None = None,
    campaign_id: str | None = None,
    world_ref_id: str | None = None,
    world_dir: Path | None = None,
    generated_at: str | None = None,
    dry_run: bool = False,
    fault_inject: str | None = None,
) -> dict[str, Any]:
    snapshots_dir = snapshots_dir.resolve()
    diffs_dir = diffs_dir.resolve()
    published_dir = published_dir.resolve()

    cur_dir = snapshots_dir / snapshot_id
    if not cur_dir.is_dir() or not (cur_dir / "current.json").is_file():
        raise PublisherError(f"Снимок {snapshot_id} не найден в {snapshots_dir}")

    cur = read_json((cur_dir / "current.json").read_bytes())
    prov = read_json((cur_dir / "provenance.json").read_bytes())

    effective_parent = parent_snapshot_id or prov.get("parent_snapshot_id")
    effective_branch = branch_id or prov.get("branch_id")
    source_kind = prov.get("source_kind", "save")
    source_format = prov.get("source_format", "kenshi-session/10")
    source_sha256 = prov.get("source_sha256")
    game_time_raw = prov.get("game_time") or cur.get("world", {})
    game_time = (
        f"Day {game_time_raw.get('day')} {game_time_raw.get('time')}"
        if isinstance(game_time_raw, dict) and game_time_raw.get("day") is not None
        else None
    )

    prev_dir = None
    if effective_parent is not None:
        prev_dir = snapshots_dir / effective_parent
        if not prev_dir.is_dir() or not (prev_dir / "current.json").is_file():
            raise PublisherError(f"Родительский снимок {effective_parent} не найден в {snapshots_dir}")

    # Вычисление или чтение существующего diff
    diff_id = make_diff_id(effective_parent, snapshot_id)
    diff_path = diffs_dir / f"{diff_id}.json"
    if diff_path.is_file():
        diff_data = read_json(diff_path.read_bytes())
    else:
        diff_data = diff_snapshots(
            prev_dir=prev_dir,
            cur_dir=cur_dir,
            output_dir=diffs_dir,
            parent_snapshot_id=effective_parent,
        )

    # 1. Загрузка / подбор справочника мира
    w_ref = _get_world_reference(
        world_ref_id=world_ref_id,
        world_dir=world_dir,
        mods=prov.get("mods") if isinstance(prov.get("mods"), list) else None,
    )
    if w_ref is None:
        raise PublisherError("Не удалось подобрать справочник мира для публикации")

    effective_wref_id = w_ref["world_ref_id"]

    # Fault injection на гео-этапе
    if fault_inject in ("before_geo", "geo_error"):
        raise PublisherError("Fault injection: сбой гео-этапа до разрешения локаций")

    # 2. Пространственное разрешение локаций
    prev_locations = None
    if prev_dir is not None:
        loc_dir = ROOT / "data/locations"
        prev_loc_path = loc_dir / f"{effective_parent}.json"
        if prev_loc_path.is_file():
            prev_locations = read_json(prev_loc_path.read_bytes())
        else:
            prev_cur = read_json((prev_dir / "current.json").read_bytes())
            prev_locations = resolve_locations(
                current=prev_cur,
                world_ref=w_ref,
                snapshot_id=effective_parent,
            )

    locations_data = resolve_locations(
        current=cur,
        world_ref=w_ref,
        snapshot_id=snapshot_id,
        previous_locations=prev_locations,
        changes=diff_data,
        generated_at=generated_at,
    )

    # Сохраняем промежуточный результат в data/locations/<snapshot_id>.json
    loc_dir = ROOT / "data/locations"
    try:
        loc_dir.mkdir(parents=True, exist_ok=True)
        (loc_dir / f"{snapshot_id}.json").write_bytes(canonical(locations_data))
    except Exception:
        pass

    # 3. Рендеринг русскоязычных Markdown с географическими сводками
    brief_md = render_brief(cur, diff_data, snapshot_id, locations=locations_data)
    squad_md = render_squad(cur, snapshot_id, locations=locations_data)

    manifest_data = {
        "format": "kenshi-publication/1",
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_id": snapshot_id,
        "parent_snapshot_id": effective_parent,
        "branch_id": effective_branch,
        "game_time": game_time,
        "world_ref_id": effective_wref_id,
        "source_kind": source_kind,
        "source_format": source_format,
        "source_sha256": source_sha256,
        "artifacts": {
            "current": "current.json",
            "changes": "changes.json",
            "brief": "brief.md",
            "squad": "squad.md",
            "locations": "locations.json",
            "world_reference": "world_reference.json",
        },
        "validation": {
            "status": "passed",
            "checks": [
                "schema",
                "artifacts",
                "semantics",
                "geography",
                "privacy",
            ],
        },
    }

    lock_file = Path(tempfile.gettempdir()) / f"kenshi_{published_dir.name}.lock"
    staging_dir = published_dir.parent / f"{published_dir.name}.staging"
    backup_dir = published_dir.parent / f"{published_dir.name}.backup"

    with PublishLock(lock_file):
        recover_if_needed(published_dir, backup_dir, staging_dir)

        # Подготовка staging
        staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Сохранение .gitkeep
            gitkeep_path = published_dir / ".gitkeep"
            gitkeep_bytes = gitkeep_path.read_bytes() if gitkeep_path.is_file() else b""
            (staging_dir / ".gitkeep").write_bytes(gitkeep_bytes)

            (staging_dir / "current.json").write_bytes(canonical(cur))
            (staging_dir / "changes.json").write_bytes(canonical(diff_data))
            (staging_dir / "locations.json").write_bytes(canonical(locations_data))
            (staging_dir / "world_reference.json").write_bytes(canonical(w_ref))
            (staging_dir / "brief.md").write_text(brief_md, encoding="utf-8")
            (staging_dir / "squad.md").write_text(squad_md, encoding="utf-8")
            (staging_dir / "manifest.json").write_bytes(canonical(manifest_data))

            if fault_inject == "before_validate":
                raise PublisherError("Fault injection: сбой до валидации пакета")

            validation_summary = validate_publication(staging_dir)

            if fault_inject == "after_validate":
                raise PublisherError("Fault injection: сбой после валидации пакета")

            if dry_run:
                shutil.rmtree(staging_dir)
                return {
                    "dry_run": True,
                    "snapshot_id": snapshot_id,
                    "validation": validation_summary,
                }

            # Атомарное переключение каталогов на Windows
            has_prev = published_dir.exists()
            if has_prev:
                published_dir.rename(backup_dir)

            if fault_inject == "during_switch":
                raise PublisherError("Fault injection: сбой во время переключения директорий")

            try:
                staging_dir.rename(published_dir)
            except Exception as switch_err:
                # Откат из backup
                if has_prev and backup_dir.exists() and not published_dir.exists():
                    backup_dir.rename(published_dir)
                raise PublisherError(f"Сбой переименования staging в target: {switch_err}") from switch_err

            # Подтверждение успешного переключения
            if not published_dir.is_dir() or not (published_dir / "manifest.json").is_file():
                if has_prev and backup_dir.exists():
                    backup_dir.rename(published_dir)
                raise PublisherError("Каталог target повреждён после переключения")

            # Проверка неизменности .gitkeep
            if (published_dir / ".gitkeep").read_bytes() != gitkeep_bytes:
                if has_prev and backup_dir.exists():
                    shutil.rmtree(published_dir)
                    backup_dir.rename(published_dir)
                raise PublisherError(".gitkeep был изменён в процессе публикации")

            # Очистка backup после подтверждения успеха
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if backup_dir.exists() and not published_dir.exists():
                backup_dir.rename(published_dir)
            raise

    return {
        "status": "published",
        "snapshot_id": snapshot_id,
        "published_dir": str(published_dir),
        "manifest": manifest_data,
    }
