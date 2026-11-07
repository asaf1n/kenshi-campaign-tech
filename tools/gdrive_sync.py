"""Синхронизация выгрузки и канона с Google Диском и приём входящих пакетов.

`.md` и `.csv` на Диске - нативные Google Docs и Sheets (запись через API, `tools/gdrive_native.py`), PDF и служебные
файлы (`tmp/`) пишутся через смонтированный Диск. Drive for desktop хранит `file_id` за файлом: удаление или подмена
файла через `os.replace` создаёт новый документ и разрывает привязку источников. Поэтому существующие файлы
обновляются только на месте.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
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
from tools.gdrive_native import (EXPORT_MIME, URL_BY_KIND, GoogleApi, NativeError, NativeSync, NativeUnavailable, RefusedError,
                                 kind_of, locate, normalize_export, resolve_root)
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
OS_JUNK_NAMES = frozenset({"desktop.ini", "thumbs.db"})
CHARS_TOP = 10


@dataclass(frozen=True)
class Profile:
    """Allowlist источников одного блокнота.

    `include` - пути относительно корня репозитория (`*` - в пределах каталога, `**/` - любая вложенность). Файл
    остаётся на своём пути на Диске; `root_dir` (по умолчанию пусто) на случай переноса профиля в отдельный каталог
    Диска: тогда файл получает новый `file_id` и источник блокнота подключается заново. `exclude` - точные пути,
    исключаемые из совпавших по `include`.
    """
    name: str
    title: str
    include: tuple[str, ...]
    root_dir: str = ""
    exclude: tuple[str, ...] = ()


# Решение пользователя: пока не входят; включение - осознанное. Семь таблиц справочника юнитов (около 1,15 млн
# символов) лежат в `published/tables/` (маска `*.csv` их совпала бы), но на Диск не попадают.
UNIT_REFERENCE_TABLES: tuple[str, ...] = tuple(f"published/tables/{name}.csv" for name in (
    "unit_templates", "unit_template_gear", "squad_templates", "squad_members",
    "squad_spawns", "faction_relations", "faction_hostility"))

PROFILE_GAME = Profile(
    "game", "Игровой блокнот",
    (
        "published/brief.md", "published/changes.md", "published/squad.md",
        "published/tables/*.csv", "published/tables/index.md",
        "published/screenshots/index.md", "published/screenshots/feed.md", f"published/screenshots/{PDF_NAME}",
        "Campaign/*/Kenshi_Хронология.md", "Campaign/*/Kenshi_Текущая_арка.md", "Campaign/*/Kenshi_Архетипы_и_билды.md",
        *DOCS_FILES, f"{DOCS_REFERENCE_DIR}/*.md",
    ),
    exclude=UNIT_REFERENCE_TABLES,
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
    "all": (GDRIVE_SESSION_DIR, "docs", "templates", GDRIVE_CHRONICLE_DIR), "session": (GDRIVE_SESSION_DIR,),
    "docs": ("docs", "templates"), "chronicle": (GDRIVE_CHRONICLE_DIR,),
}
CONFLICTS_DIR = "tmp/conflicts"
NATIVE_SHORTCUTS = (".gdoc", ".gsheet")
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


def is_os_junk(name: str) -> bool:
    """Служебные файлы ОС (desktop.ini, Thumbs.db) не входят ни в планы, ни в отчёт, ни в удаление."""
    return name.lower() in OS_JUNK_NAMES


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
    return {"written": 0, "unchanged": 0, "errors": [], "refused": [], "conflicts": [], "files_planned": [], **extra}


def _publish(native: NativeSync | None, root: Path, key: str, data: bytes, report: dict[str, Any], dry_run: bool,
             stamp: str | None = None) -> str | None:
    """`.md`/`.csv` - нативный файл Google, остальное (PDF) - запись на месте на смонтированном Диске."""
    if kind_of(key) is None:
        return _put(data, root / key, dry_run)
    if native is None:
        raise OSError(f"{key}: Google API недоступен, нативный файл не записан")
    return native.put(key, data, report, stamp)


def _publish_all(items: Iterable[tuple[str, Any]], native: NativeSync | None, root: Path, report: dict[str, Any],
                 dry_run: bool, stamp: str | None = None) -> None:
    """Запись набора `(ключ, читатель данных)`; сбой или отказ одного файла не прерывает остальные."""
    for key, read in items:
        try:
            _record(report, _publish(native, root, key, read(), report, dry_run, stamp), key, dry_run)
        except RefusedError as error:
            report["refused"].append(str(error))
        except (GitSyncError, OSError) as error:
            report["errors"].append(f"{key}: {error}")


def _flush_native(root: Path, native: NativeSync | None) -> None:
    """Секция `native` и id корня Диска - в sync-state (чужие секции сохраняются)."""
    if native is None or native.dry_run:
        return
    state = _read_state(root)
    state["native"] = native.entries
    state["native_root_id"] = native.root_id
    _write_state(root, state)


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
    excluded = set(profile.exclude)
    return sorted({path for path in candidates if path not in excluded and any(pattern.match(path) for pattern in patterns)})


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
            for path in published_dir.rglob("*") if path.is_file() and not is_os_junk(path.name)]


def sync_session(published_dir: Path, root: Path, entries: list[tuple[str, str]] | None = None,
                 dry_run: bool = False, native: NativeSync | None = None) -> dict[str, Any]:
    """Опубликованная часть игрового профиля: Markdown и CSV - нативные Docs/Sheets, PDF - файл; JSON и .jpg не пишутся."""
    if entries is None:
        entries = profile_entries(_published_candidates(published_dir), (PUBLISHED_PREFIX,))
    report = _new_report()
    _publish_all(((target, lambda source=source: (published_dir / source.removeprefix(PUBLISHED_PREFIX)).read_bytes())
                  for source, target in entries), native, root, report, dry_run)
    try:
        _flush_native(root, native)
    except OSError as error:
        report["errors"].append(f"{SYNC_STATE}: {error}")
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
                   dry_run: bool = False, native: NativeSync | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Файлы Campaign/ из зафиксированного клона в нативные Google Docs: Диск - копия `main`.

    `entries` - пары `(путь в клоне, путь на Диске)` из allowlist профилей; без них копируются все `*.md` каталога.
    Нативный файл, изменённый на Диске после нашей записи, сохраняется как `.md` в `tmp/conflicts/` и перезаписывается
    версией из клона (`conflicts`). Файлы только на Диске перечисляются в `only_on_drive` и не удаляются. Файлы клона с
    незакоммиченными правками пропускаются (`skipped_uncommitted`). Функция вызываема отдельно (после `ingest-inbox`).
    """
    source = companion_dir / GDRIVE_CHRONICLE_DIR
    report = _new_report(skipped_uncommitted=[], only_on_drive=[])
    if not source.is_dir():
        report["errors"].append(f"Каталог не найден: {GDRIVE_CHRONICLE_DIR}")
        return report
    try:
        dirty = _dirty_paths(companion_dir, GDRIVE_CHRONICLE_DIR)
    except (subprocess.SubprocessError, OSError) as error:
        report["errors"].append(f"git status: {error}")
        return report
    repo_files = [path for path in sorted(source.glob("*/**/*.md")) if path.is_file() and not linked(path)]
    if entries is None:
        pairs = [(path.relative_to(companion_dir).as_posix(),) * 2 for path in repo_files]
    else:
        pairs = [(src, dst) for src, dst in entries
                 if (companion_dir / src).is_file() and not linked(companion_dir / src)]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    items: list[tuple[str, Any]] = []
    for relative, drive_relative in pairs:
        path = companion_dir / relative
        if path.resolve() in dirty:
            report["skipped_uncommitted"].append(path.name)
            continue
        items.append((drive_relative, path.read_bytes))
    _publish_all(items, native, root, report, dry_run, stamp)
    known = {drive_relative for _, drive_relative in pairs} | {path.relative_to(companion_dir).as_posix() for path in repo_files}
    report["only_on_drive"] = sorted(path for path in _campaign_on_drive(root) if path not in known)
    try:
        _flush_native(root, native)
    except OSError as error:
        report["errors"].append(f"{SYNC_STATE}: {error}")
    return report


def _drive_document_paths(base: Path, area: str) -> list[str]:
    """Документы области на смонтированном Диске: `.md` и ярлыки нативных Docs (`.gdoc` -> путь `.md`)."""
    top = base / area
    if not top.is_dir():
        return []
    found = set()
    for path in top.rglob("*"):
        if path.is_file() and not is_os_junk(path.name):
            relative = path.relative_to(base).as_posix()
            if path.suffix.lower() == ".md":
                found.add(relative)
            elif path.suffix.lower() == ".gdoc":
                found.add(relative[: -len(path.suffix)] + ".md")
    return sorted(found)


def _campaign_on_drive(root: Path) -> list[str]:
    return _drive_document_paths(root, GDRIVE_CHRONICLE_DIR)


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=False)
    if result.returncode != 0:
        raise GitSyncError(f"git {args[0]}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _companion_files(companion_dir: Path, ref: str) -> list[str]:
    """Файлы клона на `ref` (пути в UTF-8, без экранирования)."""
    output = _git_bytes(companion_dir, "ls-tree", "-r", "-z", "--name-only", ref)
    return [name.decode("utf-8") for name in output.split(b"\0") if name]


def sync_docs(companion_dir: Path, root: Path, now: datetime | None = None, dry_run: bool = False,
              native: NativeSync | None = None) -> dict[str, Any]:
    """Документы профилей из `origin/main` в нативные Google Docs: Диск - копия `main`.

    Состав - `docs/` и `templates/` из allowlist профилей. Нативный файл, изменённый на Диске после нашей записи,
    сохраняется как `.md` в `tmp/conflicts/` до перезаписи. Ничего не удаляется. `tmp/sync-state.json` фиксирует SHA
    `origin/main` и sha256 набора. В dry-run клон не меняется (только `fetch`), читается `FETCH_HEAD`.
    """
    report = _new_report(files_written=[], files_unchanged=[])
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

    def reader(source: str, name: str) -> Any:
        def read() -> bytes:
            if (companion_dir / source).resolve() in dirty:
                raise OSError("незакоммиченные правки в клоне")
            data = _git_bytes(companion_dir, "show", f"{ref}:{source}")
            hashes[name] = hashlib.sha256(data).hexdigest()
            return data
        return read

    for source, name in entries:
        written, problems = report["written"], (len(report["errors"]), len(report["refused"]))
        _publish_all([(name, reader(source, name))], native, root, report, dry_run, stamp)
        if not dry_run and (len(report["errors"]), len(report["refused"])) == problems:
            report["files_written" if report["written"] != written else "files_unchanged"].append(name)
    try:
        _flush_native(root, native)
    except OSError as error:
        report["errors"].append(f"{SYNC_STATE}: {error}")
    if not report["errors"] and not dry_run:
        # Секции `native`, `synced` и прочие сохраняются.
        _write_state(root, {**_read_state(root), "origin_main": origin, "files": hashes})
    return report


def ensure_buffer(root: Path) -> None:
    """Подкаталоги буфера: приём пакетов, контрольные точки незавершённой сессии, обработанные пакеты."""
    for name in (GDRIVE_INBOX_DIR, GDRIVE_CHECKPOINTS_DIR, GDRIVE_PROCESSED_DIR):
        (root / name).mkdir(parents=True, exist_ok=True)


# all = сессия + docs + Campaign/: Диск - резервная копия main и выгрузки; конвейер синхронизирует всё.
PIPELINE_SCOPE = "all"
MANAGED_PREFIXES = tuple(f"{area}/" for area in MANAGED_AREAS)


def _base(root: Path, profile: Profile) -> Path:
    root_dir = profile.root_dir.strip("/")
    return root / root_dir if root_dir else root


def _file_chars(relative: str, on_drive: str, published_dir: Path, companion_dir: Path | None, ref: str,
                root: Path, repo_set: set[str]) -> dict[str, Any]:
    """`{path, chars}`: len(текст UTF-8) источника, который уйдёт на Диск; PDF/бинарный - None, отсутствующий - None."""
    item: dict[str, Any] = {"path": on_drive, "chars": None}
    if relative.lower().endswith(".pdf"):
        item["note"] = "binary"
        return item
    try:
        local = published_dir / relative.removeprefix(PUBLISHED_PREFIX)
        if relative.startswith(PUBLISHED_PREFIX) and local.is_file():
            data = local.read_bytes()
        elif companion_dir is not None and not relative.startswith(PUBLISHED_PREFIX) and relative in repo_set:
            data = _git_bytes(companion_dir, "show", f"{ref}:{relative}")
        elif (root / relative).is_file():
            data = (root / relative).read_bytes()
        else:
            item["note"] = "missing_locally"
            return item
        item["chars"] = len(data.decode("utf-8"))
    except UnicodeDecodeError:
        item["note"] = "binary"
    except (GitSyncError, OSError):
        item["note"] = "unreadable"
    return item


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
    repo_set = set(repo)
    listing: dict[str, Any] = {}
    in_set: set[str] = set()
    missing: set[str] = set()
    for profile in profiles:
        base = _base(root, profile)
        campaign = base / GDRIVE_CHRONICLE_DIR
        on_drive = _drive_document_paths(base, GDRIVE_CHRONICLE_DIR)
        pool = {*published, *repo, *on_drive}
        relatives = expand_profile(profile, pool)
        files = [drive_path(profile, relative) for relative in relatives]
        in_set.update(files)
        files_chars = sorted(
            (_file_chars(relative, drive_path(profile, relative), published_dir, companion_dir, ref, root, repo_set)
             for relative in relatives),
            key=lambda item: (item["chars"] is None, -(item["chars"] or 0), item["path"]))
        missing.update(pattern for pattern in profile.include if "*" not in pattern and pattern not in pool)
        listing[profile.name] = {
            "title": profile.title, "root_dir": profile.root_dir, "count": len(files), "limit": PROFILE_FILE_LIMIT,
            "over_limit": len(files) > PROFILE_FILE_LIMIT, "files": files,
            "files_chars": files_chars, "total_chars": sum(item["chars"] or 0 for item in files_chars),
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
    # Нативные файлы Google отображаются на смонтированном Диске ярлыками `.gdoc`/`.gsheet`: это не посторонние файлы.
    native_shortcuts = {key[: -len(PurePosixPath(key).suffix)] + ext
                        for key in (state.get("native") or {}) for ext in NATIVE_SHORTCUTS}
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
                if not path.is_file() or is_os_junk(path.name):
                    continue
                on_drive = path.relative_to(root).as_posix()
                relative = path.relative_to(base).as_posix()
                if on_drive in plan["in_set"] or (path.suffix.lower() in NATIVE_SHORTCUTS and on_drive in native_shortcuts):
                    continue
                deletable = (covered and area != GDRIVE_CHRONICLE_DIR and not linked(path)
                             and (relative in known or on_drive in synced))
                (to_delete if deletable else outside).append(on_drive)
    return sorted(set(to_delete)), sorted(set(outside))


def plan_legacy(root: Path, plan: dict[str, Any], areas: tuple[str, ...], native: NativeSync) -> tuple[list[str], list[str]]:
    """`(к удалению, ждут)`: прежние `.md`/`.csv` на смонтированном Диске, файлы репозитория (пары набора).

    К удалению - только файл, нативная копия которого создана и существует по API (`native.verified`); остальные
    ждут. Файлы `Campaign/`, которых нет в репозитории, сюда не попадают (пары набора строятся по репозиторию).
    """
    delete: list[str] = []
    waiting: list[str] = []
    for _, target in plan["entries"]:
        if kind_of(target) is None or not target.startswith(tuple(f"{area}/" for area in areas)):
            continue
        path = root / target
        if not path.is_file() or linked(path):
            continue
        (delete if target in native.verified else waiting).append(target)
    return sorted(set(delete)), sorted(set(waiting))


def _delete_to_trash(root: Path, relative: str) -> None:
    """Штатное удаление файла на смонтированном Диске: Drive for desktop переносит его в корзину, а не стирает."""
    os.remove(root / relative)


def sync_drive(
    root: Path | None, published_dir: Path, companion_dir: Path | None, scope: str = "all",
    archive_dir: Path | None = None, dry_run: bool = False, prune: bool = False,
    api: GoogleApi | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Синхронизация выбранной области; недоступный Диск или Google API - предупреждение, а не отказ публикации.

    `.md`/`.csv` пишутся нативными Google Docs/Sheets через API (`api` подменяется в тестах), PDF и `tmp/` - через
    смонтированный Диск `root`. `dry_run` ничего не пишет и не удаляет (API - только чтение), а печатает план. Выпавшие
    файлы и прежние `.md`/`.csv` с созданной нативной копией удаляются только с `prune`.
    """
    if root is None or not root.is_dir():
        _warn("Google Диск не найден, синхронизация пропущена (KENSHI_GDRIVE_DIR)")
        return {"status": "skipped", "reason": "gdrive_not_found"}
    state = _read_state(root)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    try:
        api = api or GoogleApi.open()
        native = NativeSync(api, dict(state.get("native") or {}), resolve_root(api, state.get("native_root_id")),
                            root, write_in_place, dry_run, stamp)
    except (NativeUnavailable, NativeError) as error:
        _warn(f"Google API недоступен, нативные файлы не синхронизированы: {error}")
        return {"status": "failed", "reason": "gdrive_api_unavailable", "error": str(error)}
    if not dry_run:
        ensure_buffer(root)
    result: dict[str, Any] = {"status": "synced", "dry_run": dry_run, "prune": prune}
    have_repo = companion_dir is not None
    if scope in ("all", "docs") and have_repo:
        result["docs"] = sync_docs(companion_dir, root, now, dry_run, native)
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
    if scope in ("all", "session"):
        published_entries = [pair for pair in plan["entries"] if pair[0].startswith(PUBLISHED_PREFIX)]
        result["session"] = sync_session(published_dir, root, published_entries, dry_run, native)
    if scope in ("all", "chronicle") and have_repo:
        campaign = [pair for pair in plan["entries"] if pair[0].startswith(f"{GDRIVE_CHRONICLE_DIR}/")]
        result["chronicle"] = sync_chronicle(companion_dir, root, campaign, dry_run, native, now)
    errors += [error for part in ("session", "chronicle") for error in result.get(part, {}).get("errors", [])]
    parts = [result[name] for name in ("session", "chronicle", "docs") if name in result]
    refused = [message for part in parts for message in part.get("refused", [])]

    areas = tuple(area for area in SCOPE_AREAS.get(scope, ()) if have_repo or area == GDRIVE_SESSION_DIR)
    to_delete, outside = plan_prune(root, plan, {**_read_state(root), "native": native.entries}, archive_dir, areas)
    legacy, legacy_waiting = plan_legacy(root, plan, areas, native)
    deleted: list[str] = []
    if prune and not dry_run and not errors:
        for relative in [*to_delete, *legacy]:
            try:
                _delete_to_trash(root, relative)
            except OSError as error:
                errors.append(f"{relative}: {error}")
                continue
            deleted.append(relative)
        to_delete = [path for path in to_delete if path not in deleted]
        legacy = [path for path in legacy if path not in deleted]
    if not dry_run and not errors and scope in ("all", "session", "docs"):
        synced = {target for _, target in plan["entries"]
                  if not target.startswith(f"{GDRIVE_CHRONICLE_DIR}/") and (root / target).is_file()}
        state = _read_state(root)
        state["synced"] = sorted((set(state.get("synced") or []) | synced) - set(deleted))
        try:
            _write_state(root, state)
        except OSError as error:
            errors.append(f"{SYNC_STATE}: {error}")

    planned = [item for part in parts for item in part.get("files_planned", [])]
    if dry_run and prune:
        planned += [f"delete {path}" for path in [*to_delete, *legacy]]
    result["profiles"] = plan["profiles"]
    current = {target for _, target in plan["entries"]}
    prefixes = tuple(f"{area}/" for area in areas)
    result["reconnect"] = {name: native.reconnect(profile["files"]) for name, profile in plan["profiles"].items()}
    result["summary"] = {
        "planned": planned, "written": sum(part.get("written", 0) for part in parts),
        "unchanged": sum(part.get("unchanged", 0) for part in parts), "deleted": deleted,
        "to_delete": to_delete, "legacy_to_delete": legacy, "legacy_waiting_native": legacy_waiting,
        "outside_set": outside, "missing_in_repo": plan["missing"], "refused": refused,
        "conflicts": [item for part in parts for item in part.get("conflicts", [])],
        "native_outside_set": sorted(key for key in native.entries if key.startswith(prefixes) and key not in current),
        "only_on_drive": result.get("chronicle", {}).get("only_on_drive", []),
    }
    for name, profile in plan["profiles"].items():
        if profile["over_limit"]:
            _warn(f"профиль {name}: {profile['count']} файлов, ориентир {profile['limit']}")
        _warn(f"профиль {name}: всего {profile['total_chars']} символов; топ-{CHARS_TOP}")
        for item in profile["files_chars"][:CHARS_TOP]:
            _warn(f"  {item['chars'] if item['chars'] is not None else '-'}\t{item['path']}"
                  + (f" ({item['note']})" if item.get("note") else ""))
    if refused:
        result["status"] = "partial"
        _warn(f"отказ записи нативных файлов: {'; '.join(refused)}")
    if errors:
        result["status"] = "partial"
        _warn(f"ошибки синхронизации Google Диска: {'; '.join(errors)}")
    return result


def gdrive_export(path: str, root: Path | None, api: GoogleApi | None = None) -> tuple[bytes, dict[str, Any]]:
    """Восстановление исходника из нативной копии: экспорт Google и обратная нормализация (байт в байт).

    `path` - путь файла в репозитории с расширением (`docs/x.md`, `published/tables/y.csv`). Файл ищется в sync-state
    (`native`), иначе по именам каталогов Диска; число хвостовых LF - из sync-state либо `appProperties` файла.
    """
    kind = kind_of(path)
    if kind is None:
        raise ValueError(f"Путь должен оканчиваться на .md или .csv: {path}")
    state = _read_state(root) if root is not None and root.is_dir() else {}
    api = api or GoogleApi.open()
    entry = (state.get("native") or {}).get(path) or {}
    file_id = entry.get("file_id")
    meta = api.get_meta(file_id) if file_id else None
    if meta is None or meta.get("trashed"):
        meta = locate(api, resolve_root(api, state.get("native_root_id")), path)
    if meta is None:
        raise NativeError(f"{path}: нативный файл не найден на Диске")
    trailing = entry.get("trailing_lf")
    if trailing is None:
        trailing = (meta.get("appProperties") or {}).get("trailing_lf", 1)
    data = normalize_export(kind, api.export(str(meta["id"]), EXPORT_MIME[kind]), int(trailing))
    return data, {"path": path, "file_id": meta["id"], "kind": kind, "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "url": URL_BY_KIND[kind].format(meta["id"])}


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
