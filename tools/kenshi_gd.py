"""Чтение бинарного GameData Kenshi без файловых побочных эффектов.

read_file читает заголовок 15 (.save, .platoon, .zone), read_mod — 16/17
(.base, .mod). Неразобранный хвост сейва сохраняется через consumed/size:
достижение конца списка записей не означает разбор всего файла.
Повреждённые длины, счётчики, смещения и строки вызывают ValueError.
"""
from __future__ import annotations

import struct
from typing import Callable, NotRequired, TypeVar, TypedDict, cast


class Reference(TypedDict):
    sid: str
    v: list[int]


class Instance(TypedDict):
    id: str
    target: str
    pos: list[float]
    rot: list[float]
    states: list[str]


# Функциональная форма не затеняет типы bool, float и int именами полей.
Record = TypedDict("Record", {
    "flags0": int,
    "type": int,
    "id": int,
    "name": str,
    "sid": str,
    "flags1": int,
    "bool": dict[str, bool],
    "float": dict[str, float],
    "int": dict[str, int],
    "vec3": dict[str, list[float]],
    "vec4": dict[str, list[float]],
    "string": dict[str, str],
    "file": dict[str, str],
    "refs": dict[str, list[Reference]],
    "instances": list[Instance],
})


class GameDataHeader(TypedDict):
    fileType: int
    lastId: int
    count: int
    version: NotRequired[int]
    author: NotRequired[str]
    description: NotRequired[str]
    dependencies: NotRequired[str]
    references: NotRequired[str]


class GameDataFile(TypedDict):
    header: GameDataHeader
    records: list[Record]
    consumed: int
    size: int


class Reader:
    def __init__(self, data: bytes) -> None:
        self.d: bytes = data
        self.p: int = 0

    def _require(self, size: int) -> None:
        if size < 0 or not 0 <= self.p <= len(self.d) or size > len(self.d) - self.p:
            raise ValueError(
                f"Недопустимый диапазон: смещение {self.p}, длина {size}, "
                f"размер данных {len(self.d)}"
            )

    def skip(self, size: int) -> None:
        self._require(size)
        self.p += size

    def i32(self) -> int:
        self._require(4)
        value = cast(int, struct.unpack_from("<i", self.d, self.p)[0])
        self.p += 4
        return value

    def f32(self) -> float:
        self._require(4)
        value = cast(float, struct.unpack_from("<f", self.d, self.p)[0])
        self.p += 4
        return value

    def u8(self) -> int:
        self._require(1)
        value = self.d[self.p]
        self.p += 1
        return value

    def s(self) -> str:
        size = self.i32()
        self._require(size)
        raw = self.d[self.p:self.p + size]
        try:
            value = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            try:
                value = raw.decode("cp1251", errors="strict")
            except UnicodeDecodeError:
                value = raw.decode("cp1251", errors="replace")
        self.p += size
        return value

    def eof(self) -> bool:
        self._require(0)
        return self.p == len(self.d)

    def _count(self, minimum_size: int, label: str) -> int:
        count = self.i32()
        # Нижняя граница размера элемента не даёт повреждённому счётчику
        # запустить огромный цикл или превратить отрицательное число в пустой список.
        if count < 0 or count > (len(self.d) - self.p) // minimum_size:
            raise ValueError(f"Недопустимое число {label}: {count}, смещение {self.p - 4}")
        return count


T = TypeVar("T")


def _dict(r: Reader, value: Callable[[], T], minimum_size: int) -> dict[str, T]:
    return {r.s(): value() for _ in range(r._count(4 + minimum_size, "полей"))}


def read_record(r: Reader) -> Record:
    flags0, kind, record_id = r.i32(), r.i32(), r.i32()
    name, sid, flags1 = r.s(), r.s(), r.i32()
    rec: Record = {
        "flags0": flags0,
        "type": kind,
        "id": record_id,
        "name": name,
        "sid": sid,
        "flags1": flags1,
        "bool": _dict(r, lambda: bool(r.u8()), 1),
        "float": _dict(r, r.f32, 4),
        "int": _dict(r, r.i32, 4),
        "vec3": _dict(r, lambda: [r.f32() for _ in range(3)], 12),
        "vec4": _dict(r, lambda: [r.f32() for _ in range(4)], 16),
        "string": _dict(r, r.s, 4),
        "file": _dict(r, r.s, 4),
        "refs": {},
        "instances": [],
    }
    for _ in range(r._count(8, "категорий ссылок")):
        category = r.s()
        rec["refs"][category] = [
            {"sid": r.s(), "v": [r.i32(), r.i32(), r.i32()]}
            for _ in range(r._count(16, "ссылок"))
        ]
    for _ in range(r._count(40, "инстансов")):
        instance: Instance = {
            "id": r.s(),
            "target": r.s(),
            "pos": [r.f32() for _ in range(3)],
            "rot": [r.f32() for _ in range(4)],
            "states": [r.s() for _ in range(r._count(4, "состояний"))],
        }
        rec["instances"].append(instance)
    return rec


def _read_records(r: Reader, header: GameDataHeader) -> GameDataFile:
    records = [read_record(r) for _ in range(header["count"])]
    return {"header": header, "records": records, "consumed": r.p, "size": len(r.d)}


def read_file(data: bytes) -> GameDataFile:
    """Разобрать записи сейва/зоны типа 15 или 16, оставив неизвестный хвост за consumed."""
    r = Reader(data)
    file_type = r.i32()
    if file_type not in (15, 16):
        raise ValueError(f"Неизвестный fileType сейва/зоны: {file_type}")
    header: GameDataHeader = {
        "fileType": file_type,
        "lastId": r.i32(),
        # Минимальная запись: 24 байта идентификаторов и 9 счётчиков по 4 байта.
        "count": r._count(60, "записей"),
    }
    return _read_records(r, header)


def read_mod(data: bytes) -> GameDataFile:
    """Разобрать .base/.mod: тип 16 с метаданными либо 17 с блоком заголовка."""
    r = Reader(data)
    file_type = r.i32()
    header: GameDataHeader = {"fileType": file_type, "lastId": 0, "count": 0}
    if file_type == 17:
        r.skip(r.i32())
    elif file_type == 16:
        header["version"] = r.i32()
        header["author"] = r.s()
        header["description"] = r.s()
        header["dependencies"] = r.s()
        header["references"] = r.s()
    else:
        raise ValueError(f"Неизвестный fileType мода: {file_type}")
    header["lastId"] = r.i32()
    header["count"] = r._count(60, "записей")
    return _read_records(r, header)
