"""Односторонняя доставка проверенного релиз-бандла published/ в игровой репозиторий Kenshi-Compain."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from jsonschema import ValidationError

from tools import cartography
from tools.encounters import OUTPUT_RELATIVE, build_encounters, load_tables, missing_tables
from tools.git_sync import GitSyncError, abort_unfinished, prepare_clone, push_delivery, require_git, run_git
from tools.io_utils import ExtractionError, canonical, linked, read_json
from tools.location_registry import (PROGRESS_RELATIVE, REGISTRY_FILENAME, merge_progress, registry_bytes,
                                     validate_progress, validate_registry)
from tools.unique_entities_registry import apply_progress_to_unique_entities
from tools.validation import load_entities_progress, load_location_registry, validate_world_reference

DEFAULT_COMPANION_DIR = Path(r"C:\Users\artem\Kenshi Compain")
DEFAULT_PUBLISHED_DIR = Path(__file__).resolve().parents[1] / "published"
ENTITIES_PROGRESS_RELATIVE = Path("Campaign/Хроники Хот Лон/entities_progress.json")

# Каталоги выпуска, которые синхронизируются целиком (устаревшие файлы внутри удаляются).
SYNCED_DIRS = ("screenshots", "extended", "tables")
# Каталоги прежних выпусков: удаляются в целевом репозитории целиком (ссылки и junction не затрагиваются).
LEGACY_DIRS = ("current",)
# Корневые файлы прежних выпусков: удаляются в целевом репозитории, если в публикации их больше нет.
LEGACY_ARTIFACTS = ("current.json",)

# Локальные артефакты, исключаемые из публикации в репозиторий.
LOCAL_ONLY = {Path("screenshots_latest.pdf")}

REQUIRED_ARTIFACTS = (
    "manifest.json",
    "changes.json",
    "changes.md",
    "locations.json",
    "world_reference.json",
    "timeline.jsonl",
    "brief.md",
    "squad.md",
)


def _prune(directory: Path, base: Path, keep: set[Path]) -> None:
    """Удаляет файлы вне keep и пустые каталоги; ссылки и junction не затрагиваются и не обходятся."""
    for entry in list(directory.iterdir()):
        if linked(entry):
            continue
        if entry.is_dir():
            _prune(entry, base, keep)
            if not any(entry.iterdir()):
                entry.rmdir()
        elif entry.is_file() and entry.relative_to(base) not in keep:
            entry.unlink()


def _sync_dir(src: Path, dst: Path) -> None:
    """Копирует файлы каталога выпуска и удаляет в целевом каталоге файлы, которых нет в публикации.

    Целевой каталог, являющийся ссылкой, и ссылки внутри него пропускаются: запись и удаление идут только
    по обычным каталогам и файлам.
    """
    if dst.exists() and linked(dst):
        return
    src_files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()} - LOCAL_ONLY
    for rel in src_files:
        target = dst / rel
        if any(linked(parent) for parent in target.parents if parent == dst or dst in parent.parents):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
    if dst.is_dir():
        _prune(dst, dst, src_files)


def _write_encounters(pub_path: Path, target_repo: Path, game_time: str) -> str:
    """Пишет docs/reference/encounters.md из published/tables; возвращает written | unchanged | skipped.

    Файл генерируется, а не копируется: игровой репозиторий получает его в том же коммите, что и выгрузку. Выпуск без
    таблиц справочника юнитов (прежний) файл не затрагивает.
    """
    tables_dir = pub_path / "tables"
    tables = load_tables(tables_dir) if tables_dir.is_dir() else {}
    absent = missing_tables(tables)
    target = target_repo / OUTPUT_RELATIVE
    if absent:
        sys.stderr.write(f"Предупреждение: {OUTPUT_RELATIVE.as_posix()} не обновлён, нет таблиц: {', '.join(absent)}\n")
        return "skipped"
    if any(linked(parent) for parent in (target.parent, target.parent.parent) if parent.exists()):
        sys.stderr.write(f"Предупреждение: {OUTPUT_RELATIVE.as_posix()} не обновлён, каталог назначения - ссылка\n")
        return "skipped"
    data = build_encounters(tables, game_time).encode("utf-8")
    if target.is_file() and target.read_bytes() == data:
        return "unchanged"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return "written"


def _write_cartography(target_repo: Path, *, require_registry: bool) -> tuple[str, list[str]]:
    """Доставляет regions_map.svg и regions_adjacency.json в docs/reference/; возвращает (статус, ошибки).

    Сначала проверяется совместимость просмотрщика sites/map/index.html клона с этими файлами. Несовместимый
    просмотрщик блокирует запись артефактов (incompatible); отсутствие просмотрщика в клоне — не ошибка (проверка пропускается, артефакты записываются).
    """
    viewer = target_repo / cartography.VIEWER_RELATIVE
    try:
        errors = (cartography.check_viewer_compat(viewer.read_text(encoding="utf-8"), require_registry=require_registry)
                  if viewer.is_file() else [])
    except (OSError, UnicodeDecodeError) as exc:
        errors = [f"Просмотрщик не прочитан: {type(exc).__name__}"]
    if errors:
        return "incompatible", errors
    targets = {name: target_repo / rel for name, rel in cartography.DELIVERED_ARTIFACTS.items()}
    if any(linked(path) for target in targets.values() for path in (target, target.parent, target.parent.parent) if path.exists()):
        return "skipped", ["каталог назначения - ссылка"]
    changed = False
    for name, target in targets.items():
        data = (cartography.ASSET_DIR / name).read_bytes().replace(b"\r\n", b"\n")
        if target.is_file() and target.read_bytes().replace(b"\r\n", b"\n") == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed = True
    return ("written" if changed else "unchanged"), []


def _git_blob(repo: Path, revision: str, relative: Path) -> str | None:
    entry = require_git(repo, "ls-tree", "-z", revision, "--", relative.as_posix())
    if not entry:
        return None
    mode, kind, blob = entry.split("\t", 1)[0].split()
    if kind != "blob" or mode not in ("100644", "100755"):
        raise ValueError(f"Исторический {relative.name} не является обычным файлом")
    return blob


def _progress_baseline(repo: Path, campaign_id: str, world_ref: dict[str, Any]) -> dict[str, Any] | None:
    if not (repo / ".git").exists() or run_git(repo, "rev-parse", "--verify", "HEAD").returncode != 0:
        return None
    if require_git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise ValueError("Для проверки ручного слоя требуется полная история Git")
    versions = require_git(repo, "log", "--first-parent", "--full-history", "--reverse", "--format=%H",
                           "HEAD", "--", PROGRESS_RELATIVE.as_posix()).splitlines()
    if not versions:
        return None

    current_ids = {row["instance_id"] for row in world_ref["locations"]}
    historical_ids: dict[str, set[str]] = {}
    baseline = None
    last_blob = None
    for revision in versions:
        try:
            blob = _git_blob(repo, revision, PROGRESS_RELATIVE)
            if blob == last_blob:
                continue
            last_blob = blob
            if blob is None:
                continue
            progress = read_json(require_git(repo, "cat-file", "blob", blob).encode("utf-8"))
            world_blob = _git_blob(repo, revision, Path("published/world_reference.json"))
            known = current_ids
            if world_blob is not None:
                if world_blob not in historical_ids:
                    historical_world = read_json(require_git(repo, "cat-file", "blob", world_blob).encode("utf-8"))
                    validate_world_reference(historical_world)
                    historical_ids[world_blob] = {row["instance_id"] for row in historical_world["locations"]}
                known = current_ids | historical_ids[world_blob]
            known_world = {"world_ref_id": world_ref["world_ref_id"],
                           "locations": [{"instance_id": instance_id} for instance_id in known]}
            validate_progress(progress, campaign_id, known_world, previous=baseline)
        except (ValueError, ValidationError, RecursionError):
            # Невалидная старая версия не меняет подтверждённые статусы и не мешает исправить файл.
            continue
        baseline = progress
    return baseline


def _read_progress(
    target_repo: Path, campaign_id: str | None, world_ref: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    if campaign_id != "hot-lon":
        return None, []

    progress_path = target_repo / PROGRESS_RELATIVE
    if any(linked(target_repo / path) for path in (PROGRESS_RELATIVE, *PROGRESS_RELATIVE.parents)):
        raise ValueError("Путь к ручному слою содержит ссылку")

    baseline = _progress_baseline(target_repo, campaign_id, world_ref)
    if not progress_path.exists():
        if baseline is not None:
            raise ValueError(f"Ручной слой {PROGRESS_RELATIVE.name} удалён")
        return None, []

    progress = read_json(progress_path.read_bytes())
    return progress, validate_progress(progress, campaign_id, world_ref, previous=baseline)


def _read_entities_progress(
    target_repo: Path, campaign_id: str | None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Безопасно читает и валидирует ручной слой entities_progress.json в клоне."""
    if campaign_id != "hot-lon":
        return None, "absent", "Ручной слой entities_progress отсутствует"

    progress_path = target_repo / ENTITIES_PROGRESS_RELATIVE
    try:
        if any(linked(target_repo / path) for path in (ENTITIES_PROGRESS_RELATIVE, *ENTITIES_PROGRESS_RELATIVE.parents)):
            raise ValueError("Путь к ручному слою entities_progress содержит ссылку")

        if not progress_path.exists():
            return None, "absent", "Ручной слой entities_progress отсутствует"
        if not progress_path.is_file():
            raise ValueError(f"Путь к ручному слою {ENTITIES_PROGRESS_RELATIVE.name} занят каталогом")

        progress = load_entities_progress(progress_path.read_bytes())
        if progress.get("campaign_id") != campaign_id:
            raise ValueError(
                f"Ручной слой entities_progress относится к кампании {progress.get('campaign_id')}, ожидается {campaign_id}"
            )
        return progress, "ok", None
    except ValidationError as exc:
        field = ".".join(map(str, exc.absolute_path)) or "корень файла"
        return None, "invalid", f"Нарушение схемы ручного слоя entities_progress в {field} ({exc.validator})"
    except (ExtractionError, ValueError, OSError) as exc:
        return None, "invalid", str(exc)


def _read_release_world(pub_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    path = pub_path / "world_reference.json"
    if manifest.get("artifacts", {}).get("world_reference") != path.name or linked(path):
        raise ValueError("Требуется обычный world_reference.json из манифеста выпуска")
    world_ref = read_json(path.read_bytes())
    validate_world_reference(world_ref)
    if world_ref["world_ref_id"] != manifest.get("world_ref_id"):
        raise ValueError("world_reference.json относится к другой версии мира")
    return world_ref


def check_location_progress(published_dir: Path | None = None, companion_dir: Path | None = None) -> dict[str, Any]:
    """Проверяет ручной слой клона относительно справочника и истории Git."""
    pub_path = (published_dir or DEFAULT_PUBLISHED_DIR).resolve()
    manifest = read_json((pub_path / "manifest.json").read_bytes())
    if manifest.get("validation", {}).get("status") != "passed":
        raise ValueError("Выпуск не прошёл валидацию")
    world_ref = _read_release_world(pub_path, manifest)
    progress, unmatched = _read_progress(companion_root(companion_dir), manifest.get("campaign_id"), world_ref)
    return {
        "status": "passed" if progress is not None else "absent",
        "entries": len(progress["locations"]) if progress is not None else 0,
        "unmatched_instance_ids": unmatched,
    }


def _write_release(
    pub_path: Path, target_repo: Path, game_time: str, registry: bytes | None,
    unique_entities: bytes | None = None,
    unique_entities_csv: bytes | None = None,
    progress_status: str = "ok",
    entities_progress_status: str = "absent",
) -> tuple[str, str, list[str]]:
    """Записывает published/, encounters.md и картографические артефакты в клон; идемпотентно.

    Возвращает (статус encounters, статус картографии, ошибки картографии)."""
    target_published = target_repo / "published"
    registry_target = target_published / REGISTRY_FILENAME
    target_entities = target_published / "unique_entities_registry.json"
    if linked(target_published) or linked(registry_target) or linked(target_entities):
        raise ValueError("Путь назначения реестра содержит ссылку")
    if registry_target.exists() and not registry_target.is_file():
        raise ValueError(f"Путь назначения {REGISTRY_FILENAME} занят каталогом")
    if target_entities.exists() and not target_entities.is_file():
        raise ValueError("Путь назначения unique_entities_registry.json занят каталогом")
    target_published.mkdir(parents=True, exist_ok=True)

    # 1. Корневые артефакты и .gitkeep
    for art in REQUIRED_ARTIFACTS:
        src = pub_path / art
        if src.is_file():
            shutil.copy2(src, target_published / art)
    for legacy in LEGACY_ARTIFACTS:
        if not (pub_path / legacy).is_file():
            (target_published / legacy).unlink(missing_ok=True)
    if progress_status == "invalid":
        # При невалидном ручном слое локаций блокируем перезапись location_registry.json
        pass
    else:
        if registry is None:
            registry_target.unlink(missing_ok=True)
        else:
            registry_target.write_bytes(registry)

    block_entities = (progress_status == "invalid" or entities_progress_status == "invalid")
    if block_entities:
        # При невалидном ручном слое блокируем перезапись unique_entities_registry.json
        pass
    else:
        if unique_entities is None:
            target_entities.unlink(missing_ok=True)
        else:
            target_entities.write_bytes(unique_entities)

    gitkeep = pub_path / ".gitkeep"
    if gitkeep.is_file():
        shutil.copy2(gitkeep, target_published / ".gitkeep")

    # 2. Каталоги выпуска; удаление устаревших файлов строго внутри этих каталогов
    csv_target = target_published / "tables" / "unique_entities.csv"
    saved_csv = csv_target.read_bytes() if (block_entities and csv_target.is_file()) else None

    for name in SYNCED_DIRS:
        if (pub_path / name).is_dir():
            _sync_dir(pub_path / name, target_published / name)

    if block_entities:
        if saved_csv is not None:
            csv_target.write_bytes(saved_csv)
        else:
            csv_target.unlink(missing_ok=True)
    elif unique_entities_csv is not None:
        csv_target.parent.mkdir(parents=True, exist_ok=True)
        csv_target.write_bytes(unique_entities_csv)

    for name in LEGACY_DIRS:
        legacy_dir = target_published / name
        if legacy_dir.is_dir() and not linked(legacy_dir):
            _prune(legacy_dir, legacy_dir, set())
            if not any(legacy_dir.iterdir()):
                legacy_dir.rmdir()

    try:
        encounters = _write_encounters(pub_path, target_repo, game_time)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Предупреждение: {OUTPUT_RELATIVE.as_posix()} не обновлён: {exc}\n")
        encounters = "skipped"
    try:
        carto, carto_errors = _write_cartography(target_repo, require_registry=registry is not None)
    except OSError as exc:
        carto, carto_errors = "skipped", [f"Ошибка записи картографии: {exc}"]
    return encounters, carto, carto_errors


def _commit_release(target_repo: Path, game_time: str, snap_short: str) -> bool:
    paths = ["published/"]
    if (target_repo / OUTPUT_RELATIVE).is_file():
        paths.append(OUTPUT_RELATIVE.as_posix())
    paths += [rel.as_posix() for rel in cartography.DELIVERED_ARTIFACTS.values() if (target_repo / rel).is_file()]
    require_git(target_repo, "add", *paths)
    if run_git(target_repo, "diff", "--cached", "--quiet").returncode == 0:
        return False
    require_git(target_repo, "commit", "-m", f"chore(published): доставить выгрузку {game_time} ({snap_short})")
    return True


def companion_root(companion_dir: Path | None = None) -> Path:
    return (companion_dir or Path(os.environ.get("KENSHI_COMPANION_DIR", DEFAULT_COMPANION_DIR))).resolve()


def deliver_to_companion(
    published_dir: Path | None = None,
    companion_dir: Path | None = None,
    auto_commit: bool = True,
    auto_push: bool = False,
) -> dict[str, Any]:
    pub_path = (published_dir or DEFAULT_PUBLISHED_DIR).resolve()
    target_repo = companion_root(companion_dir)
    target_published = target_repo / "published"

    if not pub_path.is_dir():
        raise FileNotFoundError(f"Каталог публикации не найден: {pub_path}")

    manifest_file = pub_path / "manifest.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Файл manifest.json не найден в {pub_path}")

    try:
        manifest = read_json(manifest_file.read_bytes())
    except Exception as exc:
        raise ValueError(f"Не удалось прочитать manifest.json: {exc}") from exc

    validation = manifest.get("validation", {})
    if validation.get("status") != "passed":
        raise ValueError(f"Публикация не прошла валидацию: status={validation.get('status')}")

    game_time = manifest.get("game_time", "Unknown")
    snapshot_id = manifest.get("snapshot_id", "")
    snap_short = snapshot_id[:15] if snapshot_id else ""
    use_git = (target_repo / ".git").is_dir() and auto_commit
    committed = False
    pushed = False
    git_error: str | None = None
    delivery_error: str | None = None
    encounters = "skipped"
    carto, carto_errors = "skipped", ["картография не выполнялась: сбой подготовки или записи выпуска"]
    progress_status = "absent"
    progress_reason: str | None = "Проверка ручного слоя не выполнена"
    entities_progress_status = "absent"
    entities_progress_reason: str | None = "Проверка ручного слоя entities_progress не выполнена"
    unmatched: list[str] = []

    def write_release() -> tuple[str, str, list[str]]:
        nonlocal progress_status, progress_reason, unmatched, entities_progress_status, entities_progress_reason
        progress_status, progress_reason, unmatched = "absent", "Проверка ручного слоя не выполнена", []
        entities_progress_status, entities_progress_reason = "absent", "Проверка ручного слоя entities_progress не выполнена"
        registry_data = None
        unique_entities_bytes = None
        unique_entities_csv_bytes = None
        world_ref = None
        progress = None
        entities_progress = None

        if "world_reference" in manifest.get("artifacts", {}) and (pub_path / "world_reference.json").is_file():
            try:
                world_ref = _read_release_world(pub_path, manifest)
            except Exception:
                world_ref = None

        has_loc_registry = "location_registry" in manifest.get("artifacts", {})
        has_entities_registry = (pub_path / "unique_entities_registry.json").is_file()

        if not (has_loc_registry or has_entities_registry):
            progress_reason = "В выпуске нет реестра локаций"
        elif world_ref is not None:
            progress_reason = "Ручной слой для кампании отсутствует"
            try:
                progress, unmatched = _read_progress(target_repo, manifest.get("campaign_id"), world_ref)
                if progress is not None:
                    progress_status, progress_reason = "ok", None
            except ValidationError as exc:
                field = ".".join(map(str, exc.absolute_path)) or "корень файла"
                progress_status, progress_reason = "invalid", f"Нарушение схемы ручного слоя в {field} ({exc.validator})"
            except (OSError, ValueError, GitSyncError, RecursionError) as exc:
                progress_status, progress_reason = "invalid", str(exc)

        entities_progress, entities_progress_status, entities_progress_reason = _read_entities_progress(
            target_repo, manifest.get("campaign_id")
        )

        if has_loc_registry:
            if world_ref is None:
                world_ref = _read_release_world(pub_path, manifest)
            registry = load_location_registry(pub_path, manifest, world_ref)
            if progress_status == "ok" and progress is not None:
                try:
                    merged = merge_progress(registry, progress)
                    validate_registry(merged, manifest, world_ref)
                    registry_data = registry_bytes(merged)
                except ValidationError as exc:
                    field = ".".join(map(str, exc.absolute_path)) or "корень файла"
                    progress_status, progress_reason = "invalid", f"Нарушение схемы ручного слоя в {field} ({exc.validator})"
                except (OSError, ValueError, GitSyncError, RecursionError) as exc:
                    progress_status, progress_reason = "invalid", str(exc)
            elif progress_status != "invalid":
                unmatched = []
                registry_data = registry_bytes(merge_progress(registry, None))

        if has_entities_registry:
            if progress_status != "invalid" and entities_progress_status != "invalid":
                entities = read_json((pub_path / "unique_entities_registry.json").read_bytes())
                entities = apply_progress_to_unique_entities(
                    entities, progress, world_ref, entities_progress_data=entities_progress
                )
                unique_entities_bytes = canonical(entities)
                from tools.tables import csv_bytes, unique_entities_table
                unique_entities_csv_bytes = csv_bytes(unique_entities_table(entities))

        return _write_release(
            pub_path, target_repo, game_time, registry_data, unique_entities_bytes,
            unique_entities_csv_bytes, progress_status, entities_progress_status,
        )

    def commit_release() -> bool:
        return _commit_release(target_repo, game_time, snap_short)

    # Порядок: fetch -> fast-forward -> запись -> коммит -> push. Запись поверх актуальной базы исключает конфликты
    # перезаписываемых файлов; при отказе подготовки клон и выгрузка не затронуты.
    prepared = False
    written = False
    try:
        if use_git:
            prepare_clone(target_repo)
            prepared = True
        encounters, carto, carto_errors = write_release()
        written = True
        warnings = validation.get("warnings", [])
        if warnings:
            sys.stderr.write(f"Предупреждения выпуска: {'; '.join(warnings)}\n")
        if use_git:
            committed = commit_release()
            if auto_push:
                # Вне условия коммита: повтор отправляет коммит, застрявший при прошлом сбое сети.
                own = run_git(target_repo, "rev-parse", "HEAD").stdout.strip() if committed else None

                def rebuild() -> None:
                    nonlocal committed, encounters, carto, carto_errors
                    encounters, carto, carto_errors = write_release()
                    committed = commit_release()

                push_delivery(target_repo, own, rebuild)
                pushed = True
    except GitSyncError as err:
        git_error = (f"{err}. Выгрузка в {target_published} {'сохранена в клоне' if written else 'не записана, клон не изменён'}; "
                     "после устранения причины повторите доставку (идемпотентно)")
        sys.stderr.buffer.write(f"WARNING: git-операции в Kenshi Compain: {git_error}\n".encode("utf-8"))
    except (OSError, ValueError) as err:
        delivery_error = str(err)
    finally:
        # Прерываем только свой rebase: чужое незавершённое состояние до prepare_clone не трогаем.
        if prepared:
            abort_unfinished(target_repo)

    is_degraded = (progress_status == "invalid" or entities_progress_status == "invalid")
    result: dict[str, Any] = {
        # Диагностика ручного слоя и карты отделена от доставки published/.
        "status": "failed" if git_error or delivery_error is not None else ("degraded" if is_degraded else "delivered"),
        "game_time": game_time,
        "snapshot_id": snapshot_id,
        "target_directory": str(target_published),
        "committed": committed,
        "pushed": pushed,
        "encounters": encounters,
        "cartography": "ok" if carto in ("written", "unchanged") else carto,
        "cartography_changed": carto == "written",
        "location_progress": progress_status,
        "location_progress_reason": progress_reason,
        "entities_progress": entities_progress_status,
        "entities_progress_reason": entities_progress_reason,
    }
    if carto in ("incompatible", "skipped"):
        result["cartography_reason"] = "; ".join(carto_errors)
        sys.stderr.buffer.write(f"WARNING: картография не доставлена ({carto}): {result['cartography_reason']}\n".encode("utf-8"))
    if git_error:
        result["git_error"] = git_error
    if delivery_error is not None:
        result["delivery_error"] = delivery_error
    if unmatched:
        result["location_progress_unmatched_instance_ids"] = unmatched
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Доставка проверенного пакета published/ в игровой репозиторий Kenshi-Compain"
    )
    parser.add_argument("--published-dir", type=Path, help="Каталог published")
    parser.add_argument("--companion-dir", type=Path, help="Каталог репозитория Kenshi Compain")
    parser.add_argument("--no-commit", action="store_true", help="Не выполнять git commit в Kenshi Compain")
    parser.add_argument("--push", action="store_true", help="Выполнить git push в Kenshi Compain")

    args = parser.parse_args()
    res = deliver_to_companion(
        published_dir=args.published_dir,
        companion_dir=args.companion_dir,
        auto_commit=not args.no_commit,
        auto_push=args.push,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
