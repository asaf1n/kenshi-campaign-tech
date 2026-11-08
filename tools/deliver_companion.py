"""Односторонняя доставка проверенного релиз-бандла published/ в игровой репозиторий Kenshi-Compain."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from tools import cartography
from tools.encounters import OUTPUT_RELATIVE, build_encounters, load_tables, missing_tables
from tools.git_sync import GitSyncError, abort_unfinished, prepare_clone, push_delivery, require_git, run_git
from tools.io_utils import linked

DEFAULT_COMPANION_DIR = Path(r"C:\Users\artem\Kenshi Compain")
DEFAULT_PUBLISHED_DIR = Path(__file__).resolve().parents[1] / "published"

# Каталоги выпуска, которые синхронизируются целиком (устаревшие файлы внутри удаляются).
SYNCED_DIRS = ("screenshots", "extended", "tables")
# Каталоги прежних выпусков: удаляются в целевом репозитории целиком (ссылки и junction не затрагиваются).
LEGACY_DIRS = ("current",)
# Корневые файлы прежних выпусков: удаляются в целевом репозитории, если в публикации их больше нет.
LEGACY_ARTIFACTS = ("current.json",)

# Пересобираемый PDF на десятки мегабайт раздувал бы историю git; он читается только с Google Диска.
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


def _write_cartography(target_repo: Path) -> tuple[str, list[str]]:
    """Доставляет regions_map.svg и regions_adjacency.json в docs/reference/; возвращает (статус, ошибки).

    Сначала проверяется совместимость просмотрщика sites/map/index.html клона с этими файлами. Несовместимый
    просмотрщик блокирует запись артефактов (incompatible); отсутствие просмотрщика (ветка ещё не влита) — не ошибка.
    """
    viewer = target_repo / cartography.VIEWER_RELATIVE
    errors = cartography.check_viewer_compat(viewer.read_text(encoding="utf-8")) if viewer.is_file() else []
    if errors:
        return "incompatible", errors
    targets = {name: target_repo / rel for name, rel in cartography.DELIVERED_ARTIFACTS.items()}
    if any(linked(parent) for target in targets.values() for parent in (target.parent, target.parent.parent) if parent.exists()):
        return "skipped", ["каталог назначения - ссылка"]
    changed = False
    for name, target in targets.items():
        data = (cartography.ASSET_DIR / name).read_bytes()
        if target.is_file() and target.read_bytes().replace(b"\r\n", b"\n") == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed = True
    return ("written" if changed else "unchanged"), []


def _write_release(pub_path: Path, target_repo: Path, game_time: str) -> tuple[str, str, list[str]]:
    """Записывает published/, encounters.md и картографические артефакты в клон; идемпотентно.

    Возвращает (статус encounters, статус картографии, ошибки картографии)."""
    target_published = target_repo / "published"
    target_published.mkdir(parents=True, exist_ok=True)

    # 1. Корневые артефакты и .gitkeep
    for art in REQUIRED_ARTIFACTS:
        src = pub_path / art
        if src.is_file():
            shutil.copy2(src, target_published / art)
    for legacy in LEGACY_ARTIFACTS:
        if not (pub_path / legacy).is_file():
            (target_published / legacy).unlink(missing_ok=True)

    gitkeep = pub_path / ".gitkeep"
    if gitkeep.is_file():
        shutil.copy2(gitkeep, target_published / ".gitkeep")

    # 2. Каталоги выпуска; удаление устаревших файлов строго внутри этих каталогов
    for name in SYNCED_DIRS:
        if (pub_path / name).is_dir():
            _sync_dir(pub_path / name, target_published / name)

    for name in LEGACY_DIRS:
        legacy_dir = target_published / name
        if legacy_dir.is_dir() and not linked(legacy_dir):
            _prune(legacy_dir, legacy_dir, set())
            if not any(legacy_dir.iterdir()):
                legacy_dir.rmdir()

    encounters = _write_encounters(pub_path, target_repo, game_time)
    carto, carto_errors = _write_cartography(target_repo)
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
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
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
    encounters = "skipped"
    carto, carto_errors = "skipped", []

    def write_release() -> tuple[str, str, list[str]]:
        return _write_release(pub_path, target_repo, game_time)

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
    finally:
        # Прерываем только свой rebase: чужое незавершённое состояние до prepare_clone не трогаем.
        if prepared:
            abort_unfinished(target_repo)

    result: dict[str, Any] = {
        "status": "failed" if git_error or carto == "incompatible" else "delivered",
        "game_time": game_time,
        "snapshot_id": snapshot_id,
        "target_directory": str(target_published),
        "committed": committed,
        "pushed": pushed,
        "encounters": encounters,
        "cartography": carto,
    }
    if carto_errors:
        result["cartography_errors"] = carto_errors
        sys.stderr.buffer.write(f"WARNING: картография не доставлена: {'; '.join(carto_errors)}\n".encode("utf-8"))
    if git_error:
        result["git_error"] = git_error
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
