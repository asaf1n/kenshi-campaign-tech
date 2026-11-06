"""Односторонняя доставка проверенного релиз-бандла published/ в игровой репозиторий Kenshi-Compain."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from tools.git_sync import GitSyncError, push_main, require_git, run_git
from tools.io_utils import linked

DEFAULT_COMPANION_DIR = Path(r"C:\Users\artem\Kenshi Compain")
DEFAULT_PUBLISHED_DIR = Path(__file__).resolve().parents[1] / "published"

# Каталоги выпуска, которые синхронизируются целиком (устаревшие файлы внутри удаляются).
SYNCED_DIRS = ("screenshots", "current", "extended", "tables")
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

    warnings = validation.get("warnings", [])
    if warnings:
        sys.stderr.write(f"Предупреждения выпуска: {'; '.join(warnings)}\n")

    committed = False
    pushed = False
    git_error: str | None = None
    game_time = manifest.get("game_time", "Unknown")
    snapshot_id = manifest.get("snapshot_id", "")
    snap_short = snapshot_id[:15] if snapshot_id else ""

    # 3. Git операции в целевом репозитории (если это git repo)
    if (target_repo / ".git").is_dir() and auto_commit:
        try:
            require_git(target_repo, "add", "published/")
            if run_git(target_repo, "diff", "--cached", "--quiet").returncode != 0:
                require_git(target_repo, "commit", "-m", f"chore(published): доставить выгрузку {game_time} ({snap_short})")
                committed = True
            if auto_push:
                # Вне условия коммита: повтор отправляет коммит, застрявший при прошлом сбое сети.
                push_main(target_repo)
                pushed = True
        except GitSyncError as err:
            git_error = str(err)
            sys.stderr.buffer.write(f"WARNING: git-операции в Kenshi Compain: {err}\n".encode("utf-8"))

    result: dict[str, Any] = {
        "status": "partial" if git_error else "delivered",
        "game_time": game_time,
        "snapshot_id": snapshot_id,
        "target_directory": str(target_published),
        "committed": committed,
        "pushed": pushed,
    }
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
