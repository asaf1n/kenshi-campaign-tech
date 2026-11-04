"""Синхронизация выгрузки и канона с локально примонтированным Google Диском и приём входящих пакетов.

Drive for desktop хранит `file_id` за файлом: удаление или подмена файла через `os.replace` создаёт новый документ и
разрывает привязку источников Gemini Notebook. Поэтому существующие файлы перезаписываются только на месте.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from tools.config import (
    GDRIVE_CHECKPOINTS_DIR,
    GDRIVE_CHRONICLE_DIR,
    GDRIVE_INBOX_DIR,
    GDRIVE_PROCESSED_DIR,
    GDRIVE_SESSION_DIR,
)
from tools.git_sync import GitSyncError, push_main, require_git, run_git, sync_with_origin
from tools.io_utils import linked
from tools.screenshot_delta import PDF_NAME
from tools.screenshot_publish import ARCHIVE_DIR

# Шарды current/ и extended/ не копируются: десятки мелких файлов перегружают очередь Drive for Desktop.
SESSION_FILES = (
    "brief.md",
    "squad.md",
    "changes.md",
    "changes.json",
    "timeline.jsonl",
    "screenshots/index.md",
    "screenshots/index.json",
    "screenshots/feed.md",
    f"screenshots/{PDF_NAME}",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
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


def _copy_all(pairs: list[tuple[Path, Path]]) -> dict[str, Any]:
    """Копирует пары; сбой одного файла не останавливает остальные и попадает в отчёт."""
    report: dict[str, Any] = {"written": 0, "unchanged": 0, "errors": []}
    for source, target in pairs:
        try:
            report["written" if write_in_place(source.read_bytes(), target) else "unchanged"] += 1
        except OSError as error:
            report["errors"].append(f"{target.name}: {error}")
    return report


def _same_frame(source: Path, target: Path) -> bool:
    """Имя кадра содержит хеш оригинала: совпадения размера достаточно, содержимое из облака не скачивается."""
    return target.is_file() and target.stat().st_size == source.stat().st_size


def sync_session(published_dir: Path, root: Path, archive_dir: Path | None = None) -> dict[str, Any]:
    """Высокоуровневый срез по тем же относительным путям, что в GitHub; кадры на Диске копятся и не удаляются."""
    session = root / GDRIVE_SESSION_DIR
    screens = session / "screenshots"
    pairs = [(published_dir / relative, session / relative) for relative in SESSION_FILES
             if (published_dir / relative).is_file()]

    bundle = published_dir / "screenshots"
    images = [path for path in sorted(bundle.iterdir()) if path.suffix.lower() in IMAGE_SUFFIXES] if bundle.is_dir() else []
    index_path = bundle / "index.json"
    new_frames = json.loads(index_path.read_text(encoding="utf-8")).get("new_frames", []) if index_path.is_file() else []
    bundled = {path.name for path in images}
    overflow = [(archive_dir or ARCHIVE_DIR) / name for name in new_frames if name not in bundled]
    pairs += [(path, screens / path.name) for path in [*images, *overflow]
              if path.is_file() and not _same_frame(path, screens / path.name)]

    report = _copy_all(pairs)
    # Манифест последним и только после полной записи: новый снимок не объявляется поверх старых файлов.
    manifest = published_dir / "manifest.json"
    if manifest.is_file() and not report["errors"]:
        last = _copy_all([(manifest, session / "manifest.json")])
        report = {key: report[key] + last[key] for key in report}
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


def sync_chronicle(companion_dir: Path, root: Path) -> dict[str, Any]:
    """Зеркало зафиксированных Markdown-документов кампаний; непроверенные правки на Диск не уходят."""
    source = companion_dir / GDRIVE_CHRONICLE_DIR
    if not source.is_dir():
        return {"written": 0, "unchanged": 0, "errors": [f"Каталог не найден: {GDRIVE_CHRONICLE_DIR}"]}
    try:
        dirty = _dirty_paths(companion_dir, GDRIVE_CHRONICLE_DIR)
    except (subprocess.SubprocessError, OSError) as error:
        return {"written": 0, "unchanged": 0, "errors": [f"git status: {error}"]}
    target = root / GDRIVE_CHRONICLE_DIR
    files = [path for path in sorted(source.glob("*/**/*.md")) if path.is_file() and not linked(path)]
    report = _copy_all([(path, target / path.relative_to(source)) for path in files if path.resolve() not in dirty])
    report["skipped_uncommitted"] = sorted(path.name for path in files if path.resolve() in dirty)
    return report


def ensure_buffer(root: Path) -> None:
    """Подкаталоги буфера: приём пакетов, контрольные точки незавершённой сессии, обработанные пакеты."""
    for name in (GDRIVE_INBOX_DIR, GDRIVE_CHECKPOINTS_DIR, GDRIVE_PROCESSED_DIR):
        (root / name).mkdir(parents=True, exist_ok=True)


def sync_drive(
    root: Path | None, published_dir: Path, companion_dir: Path | None, scope: str = "all",
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Синхронизация выбранной области; недоступный Диск — предупреждение, а не отказ публикации."""
    if root is None or not root.is_dir():
        _warn("Google Диск не найден, синхронизация пропущена (KENSHI_GDRIVE_DIR)")
        return {"status": "skipped", "reason": "gdrive_not_found"}
    ensure_buffer(root)
    result: dict[str, Any] = {"status": "synced"}
    if scope in ("all", "session"):
        result["session"] = sync_session(published_dir, root, archive_dir)
    if scope in ("all", "chronicle") and companion_dir is not None:
        result["chronicle"] = sync_chronicle(companion_dir, root)
    errors = [error for part in ("session", "chronicle") for error in result.get(part, {}).get("errors", [])]
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
        if relative.split("/")[0] != GDRIVE_CHRONICLE_DIR:
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
