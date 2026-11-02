"""Сохранения Kenshi: поиск последнего, чтение .platoon из папки или ZIP, реестр отрядов."""
import os
import zipfile
from collections.abc import Callable, Iterator
from typing import Any

from gamedb import KENSHI
from kenshi_gd import read_file
from zipnames import real_name

# (имя внутри источника, размер, кодировка имени, загрузчик байтов)
Entry = tuple[str, int, str, Callable[[], bytes]]


def iter_entries(source: str) -> Iterator[Entry]:
    """Содержимое ZIP-архива или папки сохранения (её подкаталог platoon/ и файлы верхнего уровня)."""
    if os.path.isdir(source):
        for name in sorted(os.listdir(source)):
            p = os.path.join(source, name)
            if os.path.isfile(p):
                yield name, os.path.getsize(p), "fs", _loader(p)
        pdir = os.path.join(source, "platoon")
        for name in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
            p = os.path.join(pdir, name)
            yield f"platoon/{name}", os.path.getsize(p), "fs", _loader(p)
        return
    z = zipfile.ZipFile(source)
    for info in z.infolist():
        fname, enc = real_name(info)
        yield fname, info.file_size, enc, (lambda i=info: z.read(i))


def _loader(path: str) -> Callable[[], bytes]:
    def load() -> bytes:
        with open(path, "rb") as fh:
            return fh.read()
    return load


def save_roots() -> list[str]:
    """Каталоги сохранений: пользовательский (%LOCALAPPDATA%) и каталог игры.

    Какой из них активен, задаёт 'User save location' в settings.cfg; проверяются оба,
    чтобы смена настройки не приводила к выгрузке устаревшего сохранения.
    """
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "kenshi", "save"))
    roots.append(os.path.join(KENSHI, "save"))
    return [r for r in roots if os.path.isdir(r)]


def list_saves(roots: list[str]) -> list[tuple[float, str]]:
    """Сохранения (время записи quick.save, путь), новые первыми. Без quick.save папка — не сохранение."""
    out = []
    for root in roots:
        for name in os.listdir(root):
            q = os.path.join(root, name, "quick.save")
            if os.path.isfile(q) and os.path.isdir(os.path.join(root, name, "platoon")):
                out.append((os.path.getmtime(q), os.path.join(root, name)))
    return sorted(out, reverse=True)


def signature(save_dir: str) -> tuple[Any, ...]:
    """Отпечаток состояния сохранения: меняется, если игра перезаписала его во время чтения."""
    q = os.stat(os.path.join(save_dir, "quick.save"))
    return q.st_size, q.st_mtime_ns, _dir_state(os.path.join(save_dir, "platoon")), \
        _dir_state(os.path.join(save_dir, "zone"))


def _dir_state(d: str) -> tuple[tuple[str, int], ...]:
    if not os.path.isdir(d):
        return ()
    return tuple(sorted((n, os.stat(os.path.join(d, n)).st_mtime_ns) for n in os.listdir(d)))


def read_quick_save(save_dir: str) -> dict[str, Any]:
    """Реестр отрядов и счётчики отряда игрока из quick.save за один разбор файла.

    registry: stem файла .platoon -> сведения (записи типа 34). Файлы в platoon/, которых нет
    в реестре, игра не использует: неизменённые отряды копируются из прежних сохранений.
    player_counts: 'members' и 'squads' из записи типа 56 — только для сверки.
    """
    with open(os.path.join(save_dir, "quick.save"), "rb") as fh:
        return parse_quick_save(fh.read())


def parse_quick_save(data: bytes) -> dict[str, Any]:
    """Разбор байтов quick.save: реестр отрядов, счётчики, активные моды, мир.

    mods — refs 'mods' записи 56 в порядке загрузки (base, Newwworld, Dialogue, rebirth, моды).
    world — поля записи 56 как есть; в интерфейсе игры не сверялись (см. docs/save-format.md).
    research — запись 21 (списки исследований), towns — записи 94 (handle города, имя, фракции).
    """
    res = read_file(data)
    state = next((r for r in res["records"] if r["type"] == 56), None)
    counts = {k: state["int"].get(k) for k in ("members", "squads")} if state else {}
    mods = [x["sid"] for x in state["refs"].get("mods", [])] if state else []
    world = {k: state["int"].get(k) for k in ("player money", "time day", "time hour", "time minute")}         if state else {}
    research = next((r for r in res["records"] if r["type"] == 21), None)
    return {"registry": _registry(res["records"]), "player_counts": counts, "mods": mods, "world": world,
            "research": parse_research(research) if research else None, "towns": _towns(res["records"])}


def indexed(d: dict[str, Any], prefix: str) -> list[Any]:
    """Значения ключей '<prefix>0', '<prefix>1', … в порядке номера (так игра хранит списки)."""
    n = len(prefix)
    keys = [(int(k[n:]), k) for k in d if k.startswith(prefix) and k[n:].isdigit()]
    return [d[k] for _, k in sorted(keys)]


def parse_research(rec: dict[str, Any]) -> dict[str, Any]:
    """Запись 21 quick.save: finished# / paid# / current# — sid исследований, current prog# — прогресс."""
    return {"finished": indexed(rec["string"], "finished"), "paid": indexed(rec["string"], "paid"),
            "current": indexed(rec["string"], "current"),
            "current_progress": indexed(rec["float"], "current prog"),
            "num_finished": rec["float"].get("num finished"), "num_currents": rec["float"].get("num currents")}


def town_handle(i: dict[str, int], prefix: str) -> tuple[int | None, ...]:
    return tuple(i.get(prefix + k) for k in ("TYPE", "C", "S", "I", "CS"))


def _towns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Записи 94 'Town state <имя>': handle (hand*) — ссылка из зданий (town*), 'factions here#'."""
    out = []
    for r in records:
        if r["type"] != 94:
            continue
        name = r["name"]
        out.append({"handle": town_handle(r["int"], "hand"),
                    "name": name[len("Town state "):] if name.startswith("Town state ") else name,
                    "factions_here": indexed(r["string"], "factions here")})
    return out


def read_registry(save_dir: str) -> dict[str, dict[str, Any]]:
    return read_quick_save(save_dir)["registry"]


def _registry(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    reg: dict[str, dict[str, Any]] = {}
    for r in records:
        if r["type"] != 34:
            continue
        stem = r["string"].get("platoon stringID") or r["sid"]
        reg[stem] = {
            "name": r["name"],
            "faction_sid": r["string"].get("faction stringID"),
            "faction_name": r["string"].get("faction name"),
            "squad_index": r["int"].get("squad index"),
            "dead": r["bool"].get("dead"),
        }
    return reg
