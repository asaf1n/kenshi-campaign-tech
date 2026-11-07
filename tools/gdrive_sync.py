"""Синхронизация выгрузки и канона с локально примонтированным Google Диском и приём входящих пакетов.

Drive for desktop хранит `file_id` за файлом: удаление или подмена файла через `os.replace` создаёт новый документ и
разрывает привязку источников Gemini Notebook. Поэтому существующие файлы перезаписываются только на месте.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

from tools.config import (
    GDRIVE_CHECKPOINTS_DIR,
    GDRIVE_CHRONICLE_DIR,
    GDRIVE_INBOX_DIR,
    GDRIVE_PROCESSED_DIR,
    GDRIVE_SESSION_DIR,
)
from tools.git_sync import BRANCH, GitSyncError, push_main, require_git, run_git, sync_with_origin
from tools.io_utils import linked
from tools.screenshot_delta import PDF_NAME
from tools.screenshot_publish import ARCHIVE_DIR

# Документы игрового профиля: те же относительные пути в клоне Kenshi-Compain и на Диске.
DOCS_FILES = (
    "docs/companion-guide.md",
    "docs/session-protocol.md",
    "docs/response-formatting.md",
    "docs/Kenshi_sources.md",
    "docs/Kenshi_RU_EN.md",
    "templates/template-session.md",
)
DOCS_REFERENCE_DIR = "docs/reference"
PUBLISHED_PREFIX = f"{GDRIVE_SESSION_DIR}/"
# Ориентир числа источников блокнота (лимит Gemini Notebook - 50); превышение - предупреждение, а не отказ.
PROFILE_FILE_LIMIT = 30


@dataclass(frozen=True)
class Profile:
    """Allowlist источников одного блокнота.

    `include` - пути относительно корня репозитория (`*` - в пределах каталога, `**/` - любая вложенность). Файл
    остаётся на своём пути на Диске; `root_dir` (по умолчанию пусто) на случай переноса профиля в отдельный каталог
    Диска: тогда файл получает новый `file_id` и источник блокнота подключается заново.
    """
    name: str
    title: str
    include: tuple[str, ...]
    root_dir: str = ""


PROFILE_GAME = Profile(
    "game", "Игровой блокнот",
    (
        "published/brief.md", "published/changes.md", "published/squad.md",
        "published/tables/*.csv", "published/tables/index.md",
        "published/screenshots/index.md", "published/screenshots/feed.md", f"published/screenshots/{PDF_NAME}",
        "Campaign/*/Kenshi_Хронология.md", "Campaign/*/Kenshi_Текущая_арка.md", "Campaign/*/Kenshi_Архетипы_и_билды.md",
        *DOCS_FILES, f"{DOCS_REFERENCE_DIR}/*.md",
    ),
)
PROFILE_CHRONICLE = Profile(
    "chronicle", "Блокнот летописи",
    (
        "Campaign/*/Kenshi_Хронология.md",
        "Campaign/*/archive/sessions/**/*.md", "Campaign/*/archive/Kenshi_Библия.md", "Campaign/*/archive/novel/**/*.md",
        "docs/chronicler-guide.md", "docs/Kenshi_sources.md", "docs/Kenshi_RU_EN.md",
    ),
)
PROFILES = (PROFILE_GAME, PROFILE_CHRONICLE)
# Файлы прежних версий синхронизации: уже могут лежать на Диске, в репозиториях их может не быть.
LEGACY_PUBLISHED = tuple(f"{PUBLISHED_PREFIX}{name}" for name in (
    "manifest.json", "changes.json", "timeline.jsonl", "annotations.jsonl",
    "screenshots/index.json", "screenshots/annotations.jsonl"))
# Области Диска под управлением sync; всё остальное (tmp/, ручные файлы в корне) не трогается.
MANAGED_AREAS = (GDRIVE_SESSION_DIR, "docs", "templates", GDRIVE_CHRONICLE_DIR)
SCOPE_AREAS = {
    "all": (GDRIVE_SESSION_DIR, "docs", "templates"), "session": (GDRIVE_SESSION_DIR,), "docs": ("docs", "templates"),
    "chronicle": (GDRIVE_CHRONICLE_DIR,), "pipeline": (GDRIVE_SESSION_DIR, "docs", "templates"),
}
CONFLICTS_DIR = "tmp/conflicts"
SYNC_STATE = "tmp/sync-state.json"
# Источник этих каталогов - main; пакет ingest-inbox их не меняет.
NON_PACKET_DIRS = ("docs", "prompts", "templates")
PACKET_SUFFIXES = {".md", ".txt"}

_MARKER = re.compile(r"^=== (.+?) ===\s*$")
_FILE = re.compile(r"^ФАЙЛ: (.+)$")
_SESSION = re.compile(r"^ВЫГРУЗКА (S-\d{8}-\d+)$")
_MANUAL = re.compile(r"^(СТРОКА ХРОНОЛОГИИ|ПРАВКИ: .+)$")
_CAMPAIGN = re.compile(r"^campaign:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
_END = "КОНЕЦ"


class InboxError(ValueError):
    """Пакет отклонён целиком и остаётся во входящих."""


def _warn(message: str) -> None:
    """stderr консоли Windows не в UTF-8: кириллица пишется байтами, как в CLI."""
    stream = getattr(sys.stderr, "buffer", None)
    if stream is None:
        sys.stderr.write(f"WARNING: {message}\n")
    else:
        stream.write(f"WARNING: {message}\n".encode("utf-8"))


def write_in_place(data: bytes, target: Path) -> bool:
    """Перезаписывает файл на месте, сохраняя его идентичность; совпадающее содержимое не трогает. True при записи."""
    if linked(target):
        raise OSError(f"Цель является ссылкой: {target}")
    if target.is_file() and target.stat().st_size == len(data) and target.read_bytes() == data:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as stream:
        stream.write(data)
    if target.stat().st_size != len(data):
        raise OSError(f"Размер после записи не совпал: {target}")
    return True


def _put(data: bytes, target: Path, dry_run: bool) -> str | None:
    """`create`/`write` при изменении (в dry-run - только вывод плана), None - содержимое совпало."""
    if dry_run:
        if linked(target):
            raise OSError(f"Цель является ссылкой: {target}")
        if not target.is_file():
            return "create"
        return None if target.stat().st_size == len(data) and target.read_bytes() == data else "write"
    existed = target.is_file()
    if not write_in_place(data, target):
        return None
    return "write" if existed else "create"


def _new_report(**extra: Any) -> dict[str, Any]:
    return {"written": 0, "unchanged": 0, "errors": [], "files_planned": [], **extra}


def _record(report: dict[str, Any], action: str | None, path: str, dry_run: bool) -> None:
    if action is None:
        report["unchanged"] += 1
    elif dry_run:
        report["files_planned"].append(f"{action} {path}")
    else:
        report["written"] += 1


@lru_cache(maxsize=None)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """`*` - внутри сегмента пути, `**/` - любая вложенность (в том числе нулевая)."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def expand_profile(profile: Profile, candidates: Iterable[str]) -> list[str]:
    """Пути кандидатов (относительно корня репозитория), попавшие в allowlist профиля."""
    patterns = [_glob_regex(pattern) for pattern in profile.include]
    return sorted({path for path in candidates if any(pattern.match(path) for pattern in patterns)})


def drive_path(profile: Profile, relative: str) -> str:
    root_dir = profile.root_dir.strip("/")
    return f"{root_dir}/{relative}" if root_dir else relative


def profile_entries(candidates: Iterable[str], prefixes: tuple[str, ...], profiles: tuple[Profile, ...] = PROFILES) -> list[tuple[str, str]]:
    """Пары `(путь источника, путь на Диске)` объединения профилей по префиксам источника; общий файл берётся один раз."""
    pool = [path for path in candidates if path.startswith(prefixes)]
    pairs: dict[str, str] = {}
    for profile in profiles:
        for relative in expand_profile(profile, pool):
            pairs.setdefault(drive_path(profile, relative), relative)
    return sorted((source, target) for target, source in pairs.items())


def _published_candidates(published_dir: Path) -> list[str]:
    if not published_dir.is_dir():
        return []
    return [PUBLISHED_PREFIX + path.relative_to(published_dir).as_posix()
            for path in published_dir.rglob("*") if path.is_file()]


def sync_session(published_dir: Path, root: Path, entries: list[tuple[str, str]] | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    """Опубликованная часть игрового профиля (Markdown, CSV, PDF); JSON и кадры .jpg на Диск не пишутся."""
    if entries is None:
        entries = profile_entries(_published_candidates(published_dir), (PUBLISHED_PREFIX,))
    report = _new_report()
    for source, target in entries:
        try:
            data = (published_dir / source.removeprefix(PUBLISHED_PREFIX)).read_bytes()
            _record(report, _put(data, root / target, dry_run), target, dry_run)
        except OSError as error:
            report["errors"].append(f"{target}: {error}")
    return report


def _dirty_paths(companion_dir: Path, pathspec: str) -> set[Path]:
    """Файлы с незакоммиченными изменениями в рабочем дереве игрового репозитория."""
    if not (companion_dir / ".git").exists():
        return set()
    status = subprocess.run(
        ["git", "-c", "core.quotepath=off", "status", "--porcelain", "-z", "--untracked-files=all", "--", pathspec],
        cwd=str(companion_dir), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return {(companion_dir / entry[3:]).resolve() for entry in status.stdout.split("\0") if len(entry) > 3}


CRLF, LF = bytes((13, 10)), bytes((10,))


def _normalized(data: bytes) -> bytes:
    """Drive for desktop и Git по-разному хранят окончания строк: сравнение идёт после CRLF -> LF."""
    return data.replace(CRLF, LF)


def _digest(data: bytes) -> str:
    return hashlib.sha256(_normalized(data)).hexdigest()


def _read_state(root: Path) -> dict[str, Any]:
    path = root / SYNC_STATE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(root: Path, state: dict[str, Any]) -> None:
    write_in_place((json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)).encode("utf-8"), root / SYNC_STATE)


def sync_chronicle(companion_dir: Path, root: Path, entries: list[tuple[str, str]] | None = None,
                   dry_run: bool = False) -> dict[str, Any]:
    """Файлы Campaign/ из зафиксированного клона на Диск; Диск - рабочая копия игрока, его правки не перезаписываются.

    `entries` - пары `(путь в клоне, путь на Диске)` из allowlist профилей; без них копируются все `*.md` каталога.
    Существующий файл Диска перезаписывается на месте, только если он (после CRLF -> LF) совпадает с базой из
    `tmp/sync-state.json` (секция `campaign`) или с любой версией пути в истории клона. Иначе он попадает в
    `modified_on_drive` и не трогается. Файлы только на Диске перечисляются в `only_on_drive`. Ничего не удаляется.
    """
    source = companion_dir / GDRIVE_CHRONICLE_DIR
    report = _new_report(skipped_uncommitted=[], modified_on_drive=[], only_on_drive=[])
    if not source.is_dir():
        report["errors"].append(f"Каталог не найден: {GDRIVE_CHRONICLE_DIR}")
        return report
    try:
        dirty = _dirty_paths(companion_dir, GDRIVE_CHRONICLE_DIR)
    except (subprocess.SubprocessError, OSError) as error:
        report["errors"].append(f"git status: {error}")
        return report
    target_dir = root / GDRIVE_CHRONICLE_DIR
    repo_files = [path for path in sorted(source.glob("*/**/*.md")) if path.is_file() and not linked(path)]
    if entries is None:
        pairs = [(path.relative_to(companion_dir).as_posix(),) * 2 for path in repo_files]
    else:
        pairs = [(src, dst) for src, dst in entries
                 if (companion_dir / src).is_file() and not linked(companion_dir / src)]
    state = _read_state(root)
    base: dict[str, str] = dict(state.get("campaign") or {})
    for relative, drive_relative in pairs:
        path = companion_dir / relative
        if path.resolve() in dirty:
            report["skipped_uncommitted"].append(path.name)
            continue
        target = root / drive_relative
        try:
            data = path.read_bytes()
            if target.is_file():
                current = _digest(target.read_bytes())
                if (current != _digest(data) and current != base.get(drive_relative)
                        and current not in _history_hashes(companion_dir, relative)):
                    report["modified_on_drive"].append(drive_relative)
                    continue
            _record(report, _put(data, target, dry_run), drive_relative, dry_run)
            base[drive_relative] = _digest(data)
        except (GitSyncError, OSError) as error:
            report["errors"].append(f"{path.name}: {error}")
    known = {path.relative_to(companion_dir).as_posix() for path in repo_files}
    if target_dir.is_dir():
        report["only_on_drive"] = sorted(
            path.relative_to(root).as_posix() for path in target_dir.glob("*/**/*.md")
            if path.is_file() and path.relative_to(root).as_posix() not in known)
    state["campaign"] = base
    if not dry_run:
        try:
            _write_state(root, state)
        except OSError as error:
            report["errors"].append(f"{SYNC_STATE}: {error}")
    return report


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=False)
    if result.returncode != 0:
        raise GitSyncError(f"git {args[0]}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _history_hashes(repo: Path, path: str) -> set[str]:
    """sha256 (после CRLF -> LF) всех версий пути в истории `HEAD`: совпадение - прежняя версия, а не ручная правка."""
    commits = _git_bytes(repo, "log", "--format=%H", "HEAD", "--", path).decode("ascii").split()
    hashes: set[str] = set()
    for commit in commits:
        result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=str(repo), capture_output=True, check=False)
        if result.returncode == 0:
            hashes.add(_digest(result.stdout))
    return hashes


def _companion_files(companion_dir: Path, ref: str) -> list[str]:
    """Файлы клона на `ref` (пути в UTF-8, без экранирования)."""
    output = _git_bytes(companion_dir, "ls-tree", "-r", "-z", "--name-only", ref)
    return [name.decode("utf-8") for name in output.split(b"\0") if name]


def sync_docs(companion_dir: Path, root: Path, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Документы профилей из `origin/main` на Диск: запись на месте, ручные правки Диска сохраняются в tmp/conflicts.

    Состав - `docs/` и `templates/` из allowlist профилей. Файл Диска, не совпадающий ни с одной версией пути в
    истории `main`, копируется в `tmp/conflicts/` до перезаписи. Ничего не удаляется. `tmp/sync-state.json` фиксирует
    SHA `origin/main` и sha256 записанного набора. В dry-run клон не меняется (только `fetch`), читается `FETCH_HEAD`.
    """
    report = _new_report(files_written=[], files_unchanged=[], conflicts=[])
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    try:
        if dry_run:
            require_git(companion_dir, "fetch", "origin", BRANCH)
            ref = "FETCH_HEAD"
        else:
            sync_with_origin(companion_dir)
            ref = "HEAD"
        head = require_git(companion_dir, "rev-parse", "HEAD").strip()
        origin = require_git(companion_dir, "rev-parse", "FETCH_HEAD").strip()
        if head != origin and not dry_run:
            raise GitSyncError("Клон опережает origin/main: сначала push, синхронизация документов не выполнена")
        dirty = {path for pathspec in (DOCS_REFERENCE_DIR, "docs", "templates") for path in _dirty_paths(companion_dir, pathspec)}
        entries = profile_entries(_companion_files(companion_dir, ref), ("docs/", "templates/"))
    except (GitSyncError, subprocess.SubprocessError, OSError) as error:
        report["errors"].append(str(error))
        return report
    report["origin_main"] = origin
    hashes: dict[str, str] = {}
    for source, name in entries:
        try:
            if (companion_dir / source).resolve() in dirty:
                raise OSError("незакоммиченные правки в клоне")
            data = _git_bytes(companion_dir, "show", f"{ref}:{source}")
            target = root / name
            if target.is_file() and _digest(target.read_bytes()) != _digest(data):
                if _digest(target.read_bytes()) not in _history_hashes(companion_dir, source):
                    copy = root / CONFLICTS_DIR / f"{name.removesuffix('.md')}-{stamp}.md"
                    if not dry_run:
                        write_in_place(target.read_bytes(), copy)
                    report["conflicts"].append({"path": name, "copy": copy.relative_to(root).as_posix()})
            action = _put(data, target, dry_run)
            _record(report, action, name, dry_run)
            if not dry_run:
                report["files_written" if action else "files_unchanged"].append(name)
            hashes[name] = hashlib.sha256(data).hexdigest()
        except (GitSyncError, OSError) as error:
            report["errors"].append(f"{name}: {error}")
    if not report["errors"] and not dry_run:
        # Секция `campaign` принадлежит sync_chronicle и сохраняется.
        _write_state(root, {**_read_state(root), "origin_main": origin, "files": hashes})
    return report


def ensure_buffer(root: Path) -> None:
    """Подкаталоги буфера: приём пакетов, контрольные точки незавершённой сессии, обработанные пакеты."""
    for name in (GDRIVE_INBOX_DIR, GDRIVE_CHECKPOINTS_DIR, GDRIVE_PROCESSED_DIR):
        (root / name).mkdir(parents=True, exist_ok=True)


# all и pipeline = сессия + docs; Campaign/ конвертер пишет только явной `--scope chronicle`: на Диске это рабочая копия игрока.
PIPELINE_SCOPE = "pipeline"
MANAGED_PREFIXES = tuple(f"{area}/" for area in MANAGED_AREAS)


def _base(root: Path, profile: Profile) -> Path:
    root_dir = profile.root_dir.strip("/")
    return root / root_dir if root_dir else root


def build_plan(published_dir: Path, companion_dir: Path | None, root: Path, ref: str = "HEAD",
               profiles: tuple[Profile, ...] = PROFILES) -> dict[str, Any]:
    """Состав профилей: пары источник -> Диск, списки файлов профилей, отсутствующие точные пути и репозиторные файлы.

    Файл профиля - то, что есть в `published/` технического репозитория или в клоне Kenshi-Compain на `ref`. Файлы
    `Campaign/` только на Диске, подходящие под allowlist, попадают в список профиля (для подключения источников
    блокнота), но не в пары копирования.
    """
    published = _published_candidates(published_dir)
    repo = _companion_files(companion_dir, ref) if companion_dir is not None else []
    entries = profile_entries([*published, *repo], MANAGED_PREFIXES, profiles)
    listing: dict[str, Any] = {}
    in_set: set[str] = set()
    missing: set[str] = set()
    for profile in profiles:
        base = _base(root, profile)
        campaign = base / GDRIVE_CHRONICLE_DIR
        on_drive = ([path.relative_to(base).as_posix() for path in campaign.rglob("*.md") if path.is_file()]
                    if campaign.is_dir() else [])
        pool = {*published, *repo, *on_drive}
        files = [drive_path(profile, relative) for relative in expand_profile(profile, pool)]
        in_set.update(files)
        missing.update(pattern for pattern in profile.include if "*" not in pattern and pattern not in pool)
        listing[profile.name] = {
            "title": profile.title, "root_dir": profile.root_dir, "count": len(files), "limit": PROFILE_FILE_LIMIT,
            "over_limit": len(files) > PROFILE_FILE_LIMIT, "files": files,
            # Файлы профиля с root_dir получают новые file_id: источники блокнота подключаются заново.
            "relink": files if profile.root_dir.strip("/") else [],
        }
    return {"entries": entries, "profiles": listing, "in_set": in_set, "missing": sorted(missing),
            "repo_files": set(repo), "published_files": set(published)}


def plan_prune(root: Path, plan: dict[str, Any], state: dict[str, Any], archive_dir: Path | None,
               areas: tuple[str, ...], profiles: tuple[Profile, ...] = PROFILES) -> tuple[list[str], list[str]]:
    """`(выпавшие, вне набора)`: удаляются только файлы, известные репозиториям или состоянию прежних синхронизаций.

    Известны: файлы `published/` технического репозитория, кадры локального архива, файлы клона Kenshi-Compain,
    прежний состав синхронизации и перечень `LEGACY_PUBLISHED`. `Campaign/` не удаляется никогда; остальное на Диске
    (`.gdoc`, ручные файлы) только перечисляется. Область без единого файла набора (нет выгрузки или клона) не
    чистится: пустой набор - признак сбоя, а не решение удалить всё.
    """
    archive = archive_dir or ARCHIVE_DIR
    frames = ([f"{PUBLISHED_PREFIX}screenshots/{path.name}" for path in archive.iterdir() if path.is_file()]
              if archive.is_dir() else [])
    known = {*plan["repo_files"], *plan["published_files"], *LEGACY_PUBLISHED, *frames}
    synced = set(state.get("synced") or [])
    to_delete: list[str] = []
    outside: list[str] = []
    for profile in profiles:
        base = _base(root, profile)
        for area in areas:
            top = base / area
            if not top.is_dir():
                continue
            covered = any(path.startswith(drive_path(profile, f"{area}/")) for path in plan["in_set"])
            for path in sorted(top.rglob("*")):
                if not path.is_file():
                    continue
                on_drive = path.relative_to(root).as_posix()
                relative = path.relative_to(base).as_posix()
                if on_drive in plan["in_set"]:
                    continue
                deletable = (covered and area != GDRIVE_CHRONICLE_DIR and not linked(path)
                             and (relative in known or on_drive in synced))
                (to_delete if deletable else outside).append(on_drive)
    return sorted(set(to_delete)), sorted(set(outside))


def _delete_to_trash(root: Path, relative: str) -> None:
    """Штатное удаление файла на смонтированном Диске: Drive for desktop переносит его в корзину, а не стирает."""
    os.remove(root / relative)


def sync_drive(
    root: Path | None, published_dir: Path, companion_dir: Path | None, scope: str = "all",
    archive_dir: Path | None = None, dry_run: bool = False, prune: bool = False,
) -> dict[str, Any]:
    """Синхронизация выбранной области; недоступный Диск — предупреждение, а не отказ публикации.

    `dry_run` ничего не пишет и не удаляет, а печатает план. Выпавшие файлы удаляются только с `prune`.
    """
    if root is None or not root.is_dir():
        _warn("Google Диск не найден, синхронизация пропущена (KENSHI_GDRIVE_DIR)")
        return {"status": "skipped", "reason": "gdrive_not_found"}
    if not dry_run:
        ensure_buffer(root)
    result: dict[str, Any] = {"status": "synced", "dry_run": dry_run, "prune": prune}
    have_repo = companion_dir is not None
    if scope in ("all", "docs", PIPELINE_SCOPE) and have_repo:
        result["docs"] = sync_docs(companion_dir, root, dry_run=dry_run)
    errors: list[str] = list(result.get("docs", {}).get("errors", []))
    try:
        # В dry-run клон не переносится на origin: состав читается с FETCH_HEAD (fetch выполнил sync_docs либо ниже).
        ref = "HEAD"
        if dry_run and have_repo:
            if "docs" not in result:
                require_git(companion_dir, "fetch", "origin", BRANCH)
            ref = "FETCH_HEAD"
        plan = build_plan(published_dir, companion_dir, root, ref)
    except (GitSyncError, subprocess.SubprocessError, OSError) as error:
        result["status"] = "partial"
        result["plan_error"] = str(error)
        _warn(f"состав профилей Google Диска не определён: {error}")
        return result
    if scope in ("all", "session", PIPELINE_SCOPE):
        published_entries = [pair for pair in plan["entries"] if pair[0].startswith(PUBLISHED_PREFIX)]
        result["session"] = sync_session(published_dir, root, published_entries, dry_run)
    if scope == "chronicle" and have_repo:
        campaign = [pair for pair in plan["entries"] if pair[0].startswith(f"{GDRIVE_CHRONICLE_DIR}/")]
        result["chronicle"] = sync_chronicle(companion_dir, root, campaign, dry_run)
    errors += [error for part in ("session", "chronicle") for error in result.get(part, {}).get("errors", [])]

    areas = tuple(area for area in SCOPE_AREAS.get(scope, ()) if have_repo or area == GDRIVE_SESSION_DIR)
    to_delete, outside = plan_prune(root, plan, _read_state(root), archive_dir, areas)
    deleted: list[str] = []
    if prune and not dry_run and not errors:
        for relative in list(to_delete):
            try:
                _delete_to_trash(root, relative)
            except OSError as error:
                errors.append(f"{relative}: {error}")
                continue
            deleted.append(relative)
            to_delete.remove(relative)
    if not dry_run and not errors and scope in ("all", "session", "docs", PIPELINE_SCOPE):
        synced = {target for _, target in plan["entries"]
                  if not target.startswith(f"{GDRIVE_CHRONICLE_DIR}/") and (root / target).is_file()}
        state = _read_state(root)
        state["synced"] = sorted((set(state.get("synced") or []) | synced) - set(deleted))
        try:
            _write_state(root, state)
        except OSError as error:
            errors.append(f"{SYNC_STATE}: {error}")

    parts = [result[name] for name in ("session", "chronicle", "docs") if name in result]
    planned = [item for part in parts for item in part.get("files_planned", [])]
    if dry_run and prune:
        planned += [f"delete {path}" for path in to_delete]
    result["profiles"] = plan["profiles"]
    result["summary"] = {
        "planned": planned, "written": sum(part.get("written", 0) for part in parts),
        "unchanged": sum(part.get("unchanged", 0) for part in parts), "deleted": deleted,
        "to_delete": to_delete, "outside_set": outside, "missing_in_repo": plan["missing"],
    }
    for name, profile in plan["profiles"].items():
        if profile["over_limit"]:
            _warn(f"профиль {name}: {profile['count']} файлов, ориентир {profile['limit']}")
    if errors:
        result["status"] = "partial"
        _warn(f"ошибки синхронизации Google Диска: {'; '.join(errors)}")
    return result


def parse_packet(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Блоки пакета `(вид, имя, содержимое)` и заголовки частей для ручного применения.

    Блок `ФАЙЛ` закрывает только `КОНЕЦ`: тело документа может содержать строки вида `=== Глава 2 ===`. Выгрузку
    сессии закрывает также следующий служебный блок шаблона. Незакрытый блок означает обрезанный пакет, а текст после
    закрытия или неизвестный маркер — разрыв блока либо опечатку в заголовке: молча терять их нельзя.
    """
    blocks: list[tuple[str, str, str]] = []
    manual: list[str] = []
    state: tuple[str, str] | None = None
    body: list[str] = []
    stray: list[str] = []

    def close() -> None:
        nonlocal state
        if state is not None and state[0] != "manual":
            blocks.append((state[0], state[1], "\n".join(body).strip() + "\n"))
        state = None
        body.clear()

    for line in text.splitlines():
        marker = _MARKER.match(line)
        title = marker.group(1).strip() if marker else ""
        if state is not None and state[0] == "file":
            if title == _END:
                close()
            else:
                body.append(line)
            continue
        if title == _END:
            close()
        elif _MANUAL.match(title):
            close()
            manual.append(title)
            state = ("manual", title)
        elif state is not None:
            if state[0] == "session" and (_FILE.match(title) or _SESSION.match(title)):
                raise InboxError(f"Выгрузка {state[1]} не закрыта перед блоком: {title}")
            body.append(line)
        elif _FILE.match(title):
            state = ("file", _FILE.match(title).group(1).strip())
        elif _SESSION.match(title):
            state = ("session", _SESSION.match(title).group(1))
        elif title or (blocks and line.strip() not in ("", "```")):
            stray.append(line.strip())
    if state is not None:
        raise InboxError(f"Пакет обрезан: нет завершающего === {_END} ===")
    if blocks and stray:
        raise InboxError(f"Текст вне блоков пакета: {stray[0]}")
    return blocks, manual


def _resolve_target(kind: str, name: str, content: str, companion_dir: Path) -> Path:
    """Путь записи внутри `Campaign/`: содержимое пакета — вывод модели, поэтому цели не доверяем."""
    chronicle = companion_dir / GDRIVE_CHRONICLE_DIR
    if kind == "session":
        campaign = _CAMPAIGN.search(content)
        if not campaign:
            raise InboxError(f"В выгрузке {name} нет поля campaign")
        relative = f"{GDRIVE_CHRONICLE_DIR}/{campaign.group(1)}/archive/sessions/{name}.md"
    else:
        relative = name.replace("\\", "/")
        head = relative.split("/")[0]
        if head in NON_PACKET_DIRS:
            raise InboxError(f"Каталог {head}/ не принимается пакетом (источник - main): {name}")
        if head != GDRIVE_CHRONICLE_DIR:
            # Промпты задают пути от каталога кампании; при нескольких кампаниях цель неоднозначна.
            campaigns = [path.name for path in chronicle.iterdir() if path.is_dir()] if chronicle.is_dir() else []
            if len(campaigns) != 1:
                raise InboxError(f"Путь не указывает кампанию: {name}")
            relative = f"{GDRIVE_CHRONICLE_DIR}/{campaigns[0]}/{relative}"
    parts = relative.split("/")
    if (len(parts) < 2 or any(part.strip(" .") == "" or part != part.rstrip(" .") for part in parts)
            or ":" in relative or not relative.endswith(".md")):
        raise InboxError(f"Недопустимый путь в пакете: {name}")
    target = (companion_dir / relative).resolve()
    if not target.is_relative_to(chronicle.resolve()):
        raise InboxError(f"Путь выходит за пределы {GDRIVE_CHRONICLE_DIR}/: {name}")
    return target


def _is_session_artifact(target: Path) -> bool:
    return target.parent.name == "sessions" and target.name.startswith("S-")


def apply_packet(packet: Path, companion_dir: Path) -> dict[str, Any]:
    """Проверяет все блоки, затем записывает их; при сбое записи возвращает прежнее состояние файлов."""
    blocks, manual = parse_packet(packet.read_text(encoding="utf-8-sig"))
    targets: dict[Path, bytes] = {}
    for kind, name, content in blocks:
        target = _resolve_target(kind, name, content, companion_dir)
        if target in targets:
            raise InboxError(f"Цель указана в пакете дважды: {name}")
        targets[target] = content.encode("utf-8")

    dirty = _dirty_paths(companion_dir, GDRIVE_CHRONICLE_DIR) if targets else set()
    writes: list[tuple[Path, bytes]] = []
    for target, data in targets.items():
        if target.is_file() and target.read_bytes() == data:
            continue  # Повтор применённого пакета: push или перенос могли не состояться в прошлый раз.
        if target.is_file() and _is_session_artifact(target):
            raise InboxError(f"Артефакт сессии неизменяем и уже существует: {target.name}")
        if target in dirty:
            raise InboxError(f"В целевом файле есть незакоммиченные правки: {target.name}")
        writes.append((target, data))

    undo: list[tuple[Path, bytes | None]] = []
    try:
        for target, data in writes:
            undo.append((target, target.read_bytes() if target.is_file() else None))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except OSError as error:
        for target, previous in reversed(undo):
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
        raise InboxError(f"Сбой записи, пакет откатан: {error}") from error
    base = companion_dir.resolve()
    return {"applied": [target.relative_to(base).as_posix() for target in targets], "manual": manual}


def _commit_and_push(companion_dir: Path, paths: list[str], packet_name: str) -> None:
    """Фиксирует пути пакета в main. Push выполняется и без нового коммита: прошлый запуск мог упасть на сети."""
    require_git(companion_dir, "add", "--", *paths)
    if run_git(companion_dir, "diff", "--cached", "--quiet", "--", *paths).returncode != 0:
        require_git(companion_dir, "commit", "-m", f"docs(chronicle): принять пакет {packet_name}", "--", *paths)
    push_main(companion_dir)


def ingest_inbox(root: Path | None, companion_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Принимает пакеты из `tmp/inbox/`; в `tmp/processed/` пакет переносится только после push в GitHub `main`."""
    report: dict[str, Any] = {"processed": [], "rejected": [], "ignored": []}
    if root is None or not root.is_dir():
        _warn("Google Диск не найден, приём пакетов пропущен (KENSHI_GDRIVE_DIR)")
        report["error"] = "gdrive_not_found"
        return report
    ensure_buffer(root)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    inbox = root / GDRIVE_INBOX_DIR
    packets = sorted(path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() in PACKET_SUFFIXES)
    if not packets:
        return report
    # До записи: коммит на чужой ветке не попал бы в main, а пакет ушёл бы в processed.
    try:
        sync_with_origin(companion_dir)
    except GitSyncError as error:
        report["error"] = str(error)
        return report
    for packet in packets:
        try:
            result = apply_packet(packet, companion_dir)
            if result["applied"]:
                _commit_and_push(companion_dir, result["applied"], packet.name)
        except (InboxError, GitSyncError, UnicodeDecodeError, OSError, subprocess.SubprocessError) as error:
            report["rejected"].append({"packet": packet.name, "reason": str(error)})
            continue
        if not result["applied"]:
            report["ignored"].append(packet.name)
            continue
        # Суффикс оставляет заметными пакеты с частями для ручного применения.
        mark = "-manual" if result["manual"] else ""
        try:
            # Перенос внутри Диска сохраняет file_id пакета; запрет os.replace относится к подмене содержимого.
            os.rename(packet, root / GDRIVE_PROCESSED_DIR / f"{packet.stem}-{stamp}{mark}{packet.suffix}")
        except OSError as error:
            result["move_error"] = str(error)
        report["processed"].append({"packet": packet.name, **result})
    return report
