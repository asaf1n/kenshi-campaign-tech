"""Канонические данные, хеши и границы файловых операций."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ExtractionError(ValueError):
    """Отказ без регистрации снимка."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ExtractionError("Повтор ключа JSON")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ExtractionError("Недопустимое число JSON")

    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=invalid)


def linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def tree_hashes(root: Path) -> list[list[str]]:
    """Проверяется весь набор файлов, включая не используемые парсером."""
    def raise_error(error: OSError) -> None:
        raise error

    result: list[list[str]] = []
    for directory, dirs, files in os.walk(root, onerror=raise_error):
        for name in dirs + files:
            if linked(Path(directory) / name):
                raise ExtractionError("Ссылки внутри входного каталога не поддерживаются")
        for name in files:
            path = Path(directory) / name
            result.append([path.relative_to(root).as_posix(), file_hash(path)])
    return sorted(result)


def outside(output: Path, protected: list[Path]) -> Path:
    resolved = output.resolve()
    for source in protected:
        source = source.resolve()
        if resolved == source or resolved.is_relative_to(source) or source.is_relative_to(resolved):
            raise ExtractionError("Выход пересекается с защищённым источником")
    return resolved


def install_write_guard(writable: Path) -> list[str]:
    """Ограничение операций Python; не заменяет sandbox для недоверенного native-кода."""
    import sys

    root = writable.resolve()
    denied: list[str] = []

    def check(path: Any) -> None:
        if isinstance(path, int) or not Path(os.fsdecode(path)).resolve().is_relative_to(root):
            denied.append("write_outside_workspace")
            raise PermissionError("Запись вне временного каталога запрещена")

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            mode, flags = args[1], args[2]
            if (mode and any(c in mode for c in "wax+")) or (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                check(args[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            # Ссылки не нужны конвертеру и могут обойти границу последующей записи.
            if event != "os.rename":
                denied.append(event)
                raise PermissionError("Создание ссылок запрещено")
            check(args[0])
            check(args[1])
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            check(args[0])
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen", "socket.connect", "socket.bind"}:
            denied.append(event)
            raise PermissionError("Внешние процессы, native-загрузка и сеть запрещены")

    sys.addaudithook(audit)
    return denied
