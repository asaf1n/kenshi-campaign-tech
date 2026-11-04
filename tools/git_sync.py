"""Обновление клона игрового репозитория и push в `main`.

Канон меняют и другие агенты через GitHub, поэтому клон может отставать. Без autostash: конфликт с незакоммиченной
правкой оставил бы маркеры в рабочем файле канона. Отставший клон с локальными правками получает явный отказ.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

BRANCH = "main"


class GitSyncError(RuntimeError):
    """Клон не удалось привести к `origin/main`; состояние клона не испорчено."""


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=False)


def require_git(repo: Path, *args: str) -> str:
    result = run_git(repo, *args)
    if result.returncode != 0:
        raise GitSyncError(f"git {args[0]}: {result.stderr.strip()}")
    return result.stdout


def sync_with_origin(repo: Path) -> None:
    """Проверяет ветку и переносит локальные коммиты на `origin/main`, если клон отстал; конфликт отменяет rebase."""
    branch = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != BRANCH:
        raise GitSyncError(f"Клон должен быть на ветке {BRANCH}, текущее состояние: {branch or 'detached HEAD'}")
    require_git(repo, "fetch", "origin", BRANCH)
    if run_git(repo, "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD").returncode == 0:
        return
    rebase = run_git(repo, "rebase", "FETCH_HEAD")
    if rebase.returncode != 0:
        run_git(repo, "rebase", "--abort")
        raise GitSyncError(f"git rebase: {(rebase.stderr or rebase.stdout).strip()}")


def push_main(repo: Path) -> None:
    """Отправляет `main`; при отсутствии новых коммитов ничего не меняет, поэтому безопасен при повторе."""
    sync_with_origin(repo)
    require_git(repo, "push", "origin", BRANCH)
