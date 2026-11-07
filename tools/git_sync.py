"""Обновление клона игрового репозитория и push в `main`.

Канон меняют и другие агенты через GitHub, поэтому клон может отставать. Без autostash: конфликт с незакоммиченной
правкой оставил бы маркеры в рабочем файле канона. Отставший клон с локальными правками получает явный отказ.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable

BRANCH = "main"


class GitSyncError(RuntimeError):
    """Клон не удалось привести к `origin/main`; состояние клона не испорчено."""


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=False)


def _brief(text: str) -> str:
    """Сообщение git без строк `hint:`, одной строкой."""
    return " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("hint:"))


def require_git(repo: Path, *args: str) -> str:
    result = run_git(repo, *args)
    if result.returncode != 0:
        raise GitSyncError(f"git {args[0]}: {_brief(result.stderr)}")
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


def unfinished_operation(repo: Path) -> str | None:
    """Имя незавершённой операции git в клоне (rebase, merge) либо None."""
    git_dir = repo / ".git"
    for marker, name in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge")):
        if (git_dir / marker).exists():
            return name
    return None


def abort_unfinished(repo: Path) -> None:
    """Отменяет rebase, оставленный нашей операцией; merge и чужие состояния не трогает."""
    if unfinished_operation(repo) == "rebase":
        run_git(repo, "rebase", "--abort")


def prepare_clone(repo: Path) -> None:
    """Приводит клон к `origin/main` ДО записи файлов; при любом отказе клон не изменяется.

    Порядок: защита от незавершённого rebase/merge -> ветка `main` -> fetch -> отсутствие правок отслеживаемых файлов
    -> fast-forward. Неотправленные локальные коммиты допустимы, если не расходятся с origin (повтор после сбоя сети).
    Autostash не используется.
    """
    pending = unfinished_operation(repo)
    if pending:
        raise GitSyncError(f"В клоне не завершён {pending}: выполните `git -C \"{repo}\" {pending} --abort` и повторите")
    branch = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != BRANCH:
        raise GitSyncError(f"Клон должен быть на ветке {BRANCH}, текущее состояние: {branch or 'detached HEAD'}")
    if run_git(repo, "remote", "get-url", "origin").returncode != 0:
        return
    require_git(repo, "fetch", "origin", BRANCH)
    if run_git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise GitSyncError("В клоне есть незакоммиченные правки: закоммитьте или откатите их вручную, доставка не начата")
    if run_git(repo, "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD").returncode == 0:
        return
    if run_git(repo, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD").returncode != 0:
        raise GitSyncError("Локальные коммиты клона расходятся с origin/main: разберите их вручную, доставка не начата")
    require_git(repo, "merge", "--ff-only", "FETCH_HEAD")


def push_delivery(repo: Path, own_commit: str | None, rebuild: Callable[[], None]) -> None:
    """Отправляет `main` после доставки; при отказе push (гонка) делает ровно одну повторную попытку.

    Если неотправленным был только доставочный коммит, он пересоздаётся поверх нового origin/main (reset и `rebuild`):
    конфликтов не возникает, файлы выгрузки просто записываются заново. Иначе под ним есть чужие локальные коммиты, и
    остаётся rebase; конфликт отменяет его (`rebase --abort`), клон возвращается в состояние до rebase.
    """
    if run_git(repo, "push", "origin", BRANCH).returncode == 0:
        return
    old_remote = run_git(repo, "rev-parse", "origin/main").stdout.strip()
    require_git(repo, "fetch", "origin", BRANCH)
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    parent = run_git(repo, "rev-parse", "HEAD~1").stdout.strip()
    if own_commit and head == own_commit and parent == old_remote:
        require_git(repo, "reset", "--hard", "FETCH_HEAD")
        rebuild()
    elif run_git(repo, "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD").returncode != 0:
        rebase = run_git(repo, "rebase", "FETCH_HEAD")
        if rebase.returncode != 0:
            abort_unfinished(repo)
            raise GitSyncError(f"git rebase при повторе push: {_brief(rebase.stderr or rebase.stdout)}; rebase отменён")
    retry = run_git(repo, "push", "origin", BRANCH)
    if retry.returncode != 0:
        raise GitSyncError(f"git push отклонён повторно: {_brief(retry.stderr)}")


def push_main(repo: Path) -> None:
    """Отправляет `main`; при отсутствии новых коммитов ничего не меняет, поэтому безопасен при повторе."""
    sync_with_origin(repo)
    require_git(repo, "push", "origin", BRANCH)
