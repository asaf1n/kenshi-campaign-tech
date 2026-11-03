"""Односторонняя доставка проверенного релиз-бандла published/ в игровой репозиторий Kenshi-Compain."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

DEFAULT_COMPANION_DIR = Path(r"C:\Users\artem\Kenshi Compain")
DEFAULT_PUBLISHED_DIR = Path(__file__).resolve().parents[1] / "published"

REQUIRED_ARTIFACTS = (
    "manifest.json",
    "current.json",
    "changes.json",
    "locations.json",
    "world_reference.json",
    "timeline.jsonl",
    "brief.md",
    "squad.md",
)


def deliver_to_companion(
    published_dir: Path | None = None,
    companion_dir: Path | None = None,
    auto_commit: bool = True,
    auto_push: bool = False,
) -> dict[str, Any]:
    pub_path = (published_dir or DEFAULT_PUBLISHED_DIR).resolve()
    target_repo = (
        companion_dir
        or Path(os.environ.get("KENSHI_COMPANION_DIR", DEFAULT_COMPANION_DIR))
    ).resolve()
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

    # 1. Копирование корневых артефактов
    for art in REQUIRED_ARTIFACTS:
        src = pub_path / art
        if src.is_file():
            shutil.copy2(src, target_published / art)

    # Копирование .gitkeep
    gitkeep = pub_path / ".gitkeep"
    if gitkeep.is_file():
        shutil.copy2(gitkeep, target_published / ".gitkeep")

    # 2. Копирование скриншотов, если они есть
    src_screens = pub_path / "screenshots"
    dst_screens = target_published / "screenshots"
    if src_screens.is_dir():
        dst_screens.mkdir(parents=True, exist_ok=True)
        source_names = set()
        for item in src_screens.iterdir():
            if item.is_file():
                source_names.add(item.name)
                shutil.copy2(item, dst_screens / item.name)
        # Файлы, которых нет в текущей публикации, удаляются строго внутри screenshots/.
        for stale in dst_screens.iterdir():
            if stale.is_file() and stale.name not in source_names:
                stale.unlink()

    committed = False
    pushed = False
    game_time = manifest.get("game_time", "Unknown")
    snapshot_id = manifest.get("snapshot_id", "")
    snap_short = snapshot_id[:15] if snapshot_id else ""

    # 3. Git операции в целевом репозитории (если это git repo)
    if (target_repo / ".git").is_dir() and auto_commit:
        try:
            subprocess.run(
                ["git", "add", "published/"],
                cwd=str(target_repo),
                check=True,
                capture_output=True,
                text=True,
            )
            diff_res = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(target_repo),
            )
            if diff_res.returncode != 0:
                commit_msg = f"chore(published): доставить выгрузку {game_time} ({snap_short})"
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=str(target_repo),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                committed = True
                if auto_push:
                    subprocess.run(
                        ["git", "push", "origin", "main"],
                        cwd=str(target_repo),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    pushed = True
        except Exception as err:
            sys.stderr.write(f"Предупреждение при git-операциях в Kenshi Compain: {err}\n")

    return {
        "status": "delivered",
        "game_time": game_time,
        "snapshot_id": snapshot_id,
        "target_directory": str(target_published),
        "committed": committed,
        "pushed": pushed,
    }


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
