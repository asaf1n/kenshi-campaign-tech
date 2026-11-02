"""Регрессия бинарного контракта и отказов на повреждённых данных."""
from __future__ import annotations

import struct
import unittest
from typing import Callable

from tools.kenshi_gd import GameDataFile, Reader, Record, read_file, read_mod, read_record


def integer(value: int) -> bytes:
    return struct.pack("<i", value)


def string(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return integer(len(raw)) + raw


def record_fixture(kind: int = 56) -> tuple[bytes, Record, list[int]]:
    """Эталон всех секций и смещения счётчиков для внесения повреждений."""
    expected: Record = {
        "flags0": 1, "type": kind, "id": 42, "name": "Хот Лон", "sid": "42-test.mod",
        "flags1": 2, "bool": {"alive": True, "hidden": False}, "float": {"health": 1.25},
        "int": {"money": -123}, "vec3": {"position": [1.0, -2.0, 3.5]},
        "vec4": {"rotation": [0.0, 0.0, 0.0, 1.0]}, "string": {"label": "Отряд"},
        "file": {"mesh": "data/mesh.mesh"},
        "refs": {"members": [{"sid": "7-test.mod", "v": [1, -2, 3]}]},
        "instances": [{"id": "instance-1", "target": "7-test.mod", "pos": [4.0, 5.0, 6.0],
                       "rot": [0.0, 0.0, 0.0, 1.0], "states": ["idle", "работа"]}],
    }
    raw = integer(1) + integer(kind) + integer(42) + string("Хот Лон") + string("42-test.mod") + integer(2)
    counters: list[int] = []
    sections = [
        [string("alive") + b"\x01", string("hidden") + b"\x00"],
        [string("health") + struct.pack("<f", 1.25)],
        [string("money") + integer(-123)],
        [string("position") + struct.pack("<3f", 1.0, -2.0, 3.5)],
        [string("rotation") + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)],
        [string("label") + string("Отряд")],
        [string("mesh") + string("data/mesh.mesh")],
    ]
    for entries in sections:
        counters.append(len(raw))
        raw += integer(len(entries)) + b"".join(entries)
    counters.append(len(raw))
    raw += integer(1) + string("members")
    counters.append(len(raw))
    raw += integer(1) + string("7-test.mod") + struct.pack("<3i", 1, -2, 3)
    counters.append(len(raw))
    raw += integer(1) + string("instance-1") + string("7-test.mod")
    raw += struct.pack("<7f", 4.0, 5.0, 6.0, 0.0, 0.0, 0.0, 1.0)
    counters.append(len(raw))
    raw += integer(2) + string("idle") + string("работа")
    return raw, expected, counters


def file_fixtures() -> list[tuple[str, Callable[[bytes], GameDataFile], bytes, Record]]:
    save, world, _ = record_fixture(56)
    platoon, character, _ = record_fixture(1)
    zone, building, _ = record_fixture(10)
    mod, item, _ = record_fixture(5)
    metadata = integer(7) + string("Автор") + string("Описание") + string("base") + string("ref")
    return [
        ("quick.save", read_file, struct.pack("<3i", 15, 43, 1) + save, world),
        ("squad.platoon", read_file, struct.pack("<3i", 15, 43, 1) + platoon, character),
        ("map.zone", read_file, struct.pack("<3i", 15, 43, 1) + zone, building),
        ("gamedata.base", read_mod, integer(16) + metadata + struct.pack("<2i", 43, 1) + mod, item),
        ("patch.mod", read_mod, struct.pack("<2i", 17, 6) + b"header" + struct.pack("<2i", 43, 1) + mod, item),
    ]


class GameDataTests(unittest.TestCase):
    def test_headers_records_and_unparsed_tail(self) -> None:
        for filename, parse, raw, expected in file_fixtures():
            with self.subTest(filename=filename):
                result = parse(raw + b"tail")
                header = {"fileType": struct.unpack_from("<i", raw)[0], "lastId": 43, "count": 1}
                if header["fileType"] == 16:
                    header.update(version=7, author="Автор", description="Описание", dependencies="base", references="ref")
                self.assertEqual(result, {"header": header, "records": [expected], "consumed": len(raw), "size": len(raw) + 4})
        for parse, raw in [(read_file, struct.pack("<3i", 15, 0, 0)),
                           (read_mod, struct.pack("<4i", 17, 0, 0, 0)),
                           (read_mod, struct.pack("<8i", 16, 1, 0, 0, 0, 0, 0, 0))]:
            with self.subTest(empty=raw):
                self.assertEqual(parse(raw)["records"], [])
                self.assertEqual(parse(raw)["consumed"], len(raw))

    def test_multiple_records_and_reader_position(self) -> None:
        raw, expected, _ = record_fixture()
        reader = Reader(raw + raw)
        self.assertEqual(read_record(reader), expected)
        self.assertEqual(reader.p, len(raw))
        self.assertFalse(reader.eof())
        self.assertEqual(read_record(reader), expected)
        self.assertTrue(reader.eof())
        self.assertEqual(read_file(struct.pack("<3i", 15, 43, 2) + raw + raw)["records"], [expected, expected])

    def test_strict_strings_and_scalar_boundaries(self) -> None:
        for encoded, expected in [(b"", ""), ("Отряд".encode("utf-8"), "Отряд"),
                                  ("Отряд".encode("cp1251"), "Отряд")]:
            with self.subTest(encoded=encoded):
                reader = Reader(string(encoded))
                self.assertEqual(reader.s(), expected)
                self.assertTrue(reader.eof())
        reader = Reader(struct.pack("<ifB", -2147483648, 1.25, 255))
        self.assertEqual((reader.i32(), reader.f32(), reader.u8()), (-2147483648, 1.25, 255))
        for raw in [integer(-1), integer(2147483647), integer(2) + b"a", string(b"\x98")]:
            with self.subTest(invalid_string=raw), self.assertRaises(ValueError):
                Reader(raw).s()
        for offset in (-1, 1, 5):
            for method in ("i32", "f32", "u8", "eof"):
                reader = Reader(b"" if offset == 1 else b"\x00" * 4)
                reader.p = offset
                with self.subTest(offset=offset, method=method), self.assertRaises(ValueError):
                    getattr(reader, method)()
        for size in (-1, 5):
            with self.subTest(skip=size), self.assertRaises(ValueError):
                Reader(b"\x00" * 4).skip(size)

    def test_truncated_headers_and_nested_records(self) -> None:
        # Любой неполный префикс обязан дать ValueError, а не struct.error/IndexError
        # и не частично успешный результат с потерянной секцией.
        for filename, parse, raw, _ in file_fixtures():
            for end in range(len(raw)):
                with self.subTest(filename=filename, end=end), self.assertRaises(ValueError):
                    parse(raw[:end])

    def test_invalid_counts_header_sizes_and_types(self) -> None:
        raw, _, counters = record_fixture()
        for offset in counters:
            for count in (-1, 2147483647):
                damaged = raw[:offset] + integer(count) + raw[offset + 4:]
                with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                    read_record(Reader(damaged))
        for count in (-1, 2147483647):
            for parse, header in [(read_file, struct.pack("<3i", 15, 0, count)),
                                  (read_mod, struct.pack("<4i", 17, 0, 0, count)),
                                  (read_mod, struct.pack("<8i", 16, 1, 0, 0, 0, 0, 0, count))]:
                with self.subTest(header=header), self.assertRaises(ValueError):
                    parse(header)
        for size in (-1, 2147483647):
            with self.subTest(header_size=size), self.assertRaises(ValueError):
                read_mod(struct.pack("<4i", 17, size, 0, 0))
        for parse, file_type in [(read_file, 14), (read_file, 17), (read_mod, 15), (read_mod, 18)]:
            with self.subTest(file_type=file_type, parse=parse), self.assertRaises(ValueError):
                parse(struct.pack("<3i", file_type, 0, 0))


if __name__ == "__main__":
    unittest.main()
