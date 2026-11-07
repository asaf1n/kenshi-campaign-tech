"""Контракт поиска модов, наложения шаблонов, локализации и кэша GameDB."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from tools import gamedb
from tools.kenshi_gd import read_mod


def _string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def _record(
    sid: str, name: str, kind: int = 5,
    data: dict[str, dict[str, Any]] | None = None,
    refs: dict[str, dict[str, list[int]]] | None = None,
) -> bytes:
    data = data or {}
    raw = struct.pack("<3i", 0, kind, 1) + _string(name) + _string(sid) + struct.pack("<i", 0)
    for section in ("bool", "float", "int", "vec3", "vec4", "string", "file"):
        fields = data.get(section, {})
        raw += struct.pack("<i", len(fields))
        for key, value in fields.items():
            raw += _string(key)
            if section in ("string", "file"):
                raw += _string(value)
            else:
                raw += struct.pack({"bool": "<?", "float": "<f", "int": "<i"}[section], value)
    raw += struct.pack("<i", len(refs or {}))
    for category, entries in (refs or {}).items():
        raw += _string(category) + struct.pack("<i", len(entries))
        for target, values in entries.items():
            raw += _string(target) + struct.pack("<3i", *values)
    return raw + struct.pack("<i", 0)


def _mod(*records: bytes) -> bytes:
    return struct.pack("<4i", 17, 0, 2, len(records)) + b"".join(records)


class GameDBTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="kenshi-gamedb-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.kenshi = self.root / "Игра [Kenshi]"
        self.workshop = self.root / "workshop"
        self.po = self.kenshi / "locale/ru_RU/gamedata.po"
        for directory in (self.kenshi / "data", self.kenshi / "mods", self.workshop, self.po.parent):
            directory.mkdir(parents=True)
        self.cache = self.root / "cache"

    def write(self, path: Path, content: bytes = b"") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_find_mod_and_mods_cfg(self) -> None:
        base = [self.write(self.kenshi / "data" / name) for name in gamedb.BASE_FILES.values()]
        local = self.write(self.kenshi / "mods/local/Локальный.mod")
        workshop = self.write(self.workshop / "123/Workshop.mod")
        data = self.write(self.kenshi / "data/Data.mod")
        self.write(self.workshop / "456/Локальный.mod")
        self.write(self.kenshi / "data/Локальный.mod")
        self.assertEqual(gamedb.mods_cfg(self.kenshi), [])
        self.write(self.kenshi / "data/mods.cfg", "\ufeff  Локальный.mod \n\nWorkshop.mod\n".encode("utf-8"))
        cases = list(zip(gamedb.BASE_FILES, base)) + [
            ("Локальный", local), ("Локальный.mod", local), ("Workshop.mod", workshop), ("Data", data),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(Path(gamedb.find_mod(name, self.kenshi, self.workshop)), expected)
        self.assertIsNone(gamedb.find_mod("missing", self.kenshi, self.workshop))
        self.assertEqual(gamedb.mods_cfg(self.kenshi), ["Локальный.mod", "Workshop.mod"])
        expected_mods = sorted([local, workshop, self.workshop / "456/Локальный.mod"], key=str)
        self.assertEqual([Path(p) for p in gamedb.game_files(self.kenshi, self.workshop)], base + expected_mods)
        with patch.multiple(gamedb, KENSHI=str(self.kenshi), WORKSHOP=str(self.workshop)):
            self.assertEqual(Path(gamedb.find_mod("Локальный")), local)
            self.assertEqual(gamedb.mods_cfg(), ["Локальный.mod", "Workshop.mod"])
            self.assertEqual(gamedb.game_files(), gamedb.game_files(self.kenshi, self.workshop))

    def test_active_files(self) -> None:
        base = self.write(self.kenshi / "data/gamedata.base")
        local = self.write(self.kenshi / "mods/local/Test.mod")
        self.write(self.kenshi / "data/mods.cfg", b"Test.mod\nmissing.mod\n")
        names = ["Test.mod", "base", "Test", "missing.mod"]
        active = gamedb.active_files(names, self.kenshi, self.workshop)
        self.assertEqual([Path(p) for p in active["files"]], [local, base])
        self.assertEqual(active["missing"], ["missing.mod"])
        self.assertEqual((active["source"], active["mods"]), ("quick.save", names))
        for fallback in (None, []):
            with self.subTest(fallback=fallback):
                active = gamedb.active_files(fallback, self.kenshi, self.workshop)
                self.assertEqual([Path(p) for p in active["files"]], [base, local])
                self.assertEqual(active["missing"], ["Newwworld", "Dialogue", "rebirth", "missing.mod"])
                self.assertEqual(active["source"], "mods.cfg")
        with patch.multiple(gamedb, KENSHI=str(self.kenshi), WORKSHOP=str(self.workshop)):
            self.assertEqual(gamedb.active_files(names), gamedb.active_files(names, self.kenshi, self.workshop))

    def test_load_po(self) -> None:
        self.assertEqual(gamedb.load_po(self.po), ({}, {}, {}))
        text = (
            '#. Type: ARMOUR\n#: 1-base.mod\nmsgid "Coat"\nmsgstr "Пальто"\n\n'
            '#. Type: ITEM\n#: 2-base.mod:1\nmsgid "Description"\nmsgstr "Описание"\n\n'
            '#. Type: RESEARCH\n#: 3-base.mod\nmsgid ""\n"Long "\n"name"\nmsgstr ""\n"Длинное "\n"имя"\n\n'
            '#. Type: BUILDING\n#: 4-base.mod\nmsgid "Empty"\nmsgstr ""\n'
        )
        self.po.write_text(text, encoding="utf-8")
        expected = (
            {"1-base.mod": "Пальто", "3-base.mod": "Длинное имя"},
            {"1-base.mod": "ARMOUR", "3-base.mod": "RESEARCH", "4-base.mod": "BUILDING"},
            {"Coat": "Пальто", "Description": "Описание", "Long name": "Длинное имя"},
        )
        self.assertEqual(gamedb.load_po(self.po), expected)
        with patch.object(gamedb, "PO_RU", str(self.po)):
            self.assertEqual(gamedb.load_po(), expected)

    def test_build_mod_overlay(self) -> None:
        sid = "1-base.mod"
        base = self.write(self.root / "base.mod", _mod(_record(
            sid, "Coat", data={"float": {"weight kg": 2.5, "untouched": 7.0},
                               "int": {"slot": 1}, "bool": {"flag": False}, "string": {"label": "old"}},
            refs={"part coverage": {"head": [1, 2, 3], "body": [4, 5, 6]}},
        )))
        mod = self.write(self.root / "patch.mod", _mod(_record(
            sid, "Patched coat", data={"float": {"weight kg": 3.5}, "int": {"slot": 2},
                                       "bool": {"flag": True}, "string": {"label": "new"}},
            refs={"part coverage": {"head": [7, 8, 9], "legs": [10, 11, 12]}},
        ), _record("2-patch.mod", "Patched coat"), _record("3-patch.mod", "", kind=999)))
        self.po.write_text('#. Type: ARMOUR\n#: 1-base.mod\nmsgid "Patched coat"\nmsgstr "Пальто мода"\n', encoding="utf-8")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (base, mod, self.po)}
        db = gamedb.build([base, mod], self.po)
        entry = db["records"][sid]
        self.assertEqual((entry["defined_in"], entry["modified_in"], entry["name"], entry["name_ru"]),
                         ("base.mod", ["patch.mod"], "Patched coat", "Пальто мода"))
        self.assertEqual(entry["data"], {"float": {"weight kg": 3.5, "untouched": 7.0}, "int": {"slot": 2},
                                         "bool": {"flag": True}, "string": {"label": "new"}})
        self.assertEqual(entry["refs"], {"part coverage": {"head": [7, 8, 9], "body": [4, 5, 6], "legs": [10, 11, 12]}})
        index, _ = gamedb.load_index(self.cache, [base, mod], self.po)
        self.assertEqual(index[sid], ["Patched coat", "Пальто мода", "ARMOUR", "base.mod",
                                     {"weight kg": 3.5, "slot": 2, "refs:part coverage": entry["refs"]["part coverage"]}])
        self.assertEqual(index["2-patch.mod"], ["Patched coat", "Пальто мода", "ARMOUR", "patch.mod", {}])
        self.assertEqual(index["3-patch.mod"], ["", "", "type_999", "patch.mod", None])
        self.assertEqual(gamedb.build([mod, base], self.po)["records"][sid]["name"], "Coat")
        with patch.object(gamedb, "game_files", return_value=[str(base), str(mod)]), patch.object(gamedb, "PO_RU", str(self.po)):
            self.assertEqual(gamedb.build(), db)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_item_value_follows_load_order(self) -> None:
        # Ловит выбор цены из первого файла или первого определения вместо последнего в порядке загрузки.
        sid = "1-base.mod"
        base = self.write(self.root / "base.mod", _mod(_record(sid, "Hemp", kind=4, data={"int": {"value": 50}})))
        patch_mod = self.write(self.root / "patch.mod", _mod(_record(sid, "Hemp", kind=4, data={"int": {"value": 100}})))
        silent = self.write(self.root / "silent.mod", _mod(_record(sid, "Hemp", kind=4)))
        self.po.write_text('#. Type: ITEM\n#: 1-base.mod\nmsgid "Hemp"\nmsgstr "Пенька"\n', encoding="utf-8")
        for files, value, source in (([base, patch_mod, silent], 100, "patch.mod"), ([patch_mod, base, silent], 50, "base.mod"),
                                     ([base, silent], 50, "base.mod")):
            with self.subTest(order=[f.name for f in files]):
                stats = gamedb.load_index(self.cache, files, self.po)[0][sid][4]
                self.assertEqual((stats["value"], stats["value_source"]), (value, source))
        stats = gamedb.load_index(self.cache, [base, patch_mod], self.po)[0][sid][4]
        self.assertEqual(stats["value_history"], [["base.mod", 50], ["patch.mod", 100]])

    def test_load_index_and_cache(self) -> None:
        sid = "1-base.mod"
        base = self.write(self.root / "base.mod", _mod(_record(
            sid, "Constants", kind=27, data={"float": {"weapon inventory weight mult": 2.0}},
        )))
        with patch.object(gamedb.os, "replace", wraps=os.replace) as replace:
            index, first = gamedb.load_index(self.cache, [base], self.po)
            self.assertEqual(replace.call_count, 2)  # индекс и кэш записей реакций/торговли
        self.assertEqual(index[sid], ["Constants", "Constants", "CONSTANTS", "base.mod", {"weapon inventory weight mult": 2.0}])
        self.assertEqual(first["cache"], "rebuilt")
        self.assertEqual(first["files"], 1)
        cache_file = self.cache / first["cache_file"]
        cache_bytes = cache_file.read_bytes()
        self.assertFalse(list(self.cache.glob("*.tmp")))
        fp = gamedb.fingerprint([base], self.po)
        self.assertEqual(fp, [["index_version", gamedb.INDEX_VERSION],
                              [str(base), base.stat().st_size, base.stat().st_mtime_ns], [str(self.po), None, None]])
        self.assertEqual(json.loads(cache_bytes), {"fingerprint": fp, "index": index})
        with patch.object(gamedb, "build", side_effect=AssertionError("Кэш должен читаться без парсинга")):
            cached, second = gamedb.load_index(self.cache, [base], self.po)
        self.assertEqual((cached, second["cache"], second["cache_file"]), (index, "hit", first["cache_file"]))
        self.assertEqual(cache_file.read_bytes(), cache_bytes)
        cache_file.write_text("broken json", encoding="utf-8")
        self.assertEqual(gamedb.load_index(self.cache, [base], self.po), (index, first))
        previous = first
        untranslated_cache: str | None = None
        for change in ("mtime", "content", "po_appears", "po_changes", "po_removed"):
            with self.subTest(change=change):
                if change == "mtime":
                    stat = base.stat()
                    os.utime(base, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
                elif change == "content":
                    base.write_bytes(_mod(_record(sid, "New constants", kind=27)))
                elif change == "po_appears":
                    self.po.write_text('msgid "New constants"\nmsgstr "Константы"\n', encoding="utf-8")
                elif change == "po_changes":
                    self.po.write_text('msgid "New constants"\nmsgstr "Новые константы"\n', encoding="utf-8")
                else:
                    self.po.unlink()
                current, meta = gamedb.load_index(self.cache, [base], self.po)
                # Возврат к прежнему отпечатку переиспользует сохранённый кэш.
                self.assertEqual(meta["cache"], "hit" if change == "po_removed" else "rebuilt")
                self.assertNotEqual(meta["cache_file"], previous["cache_file"])
                if change == "content":
                    untranslated_cache = meta["cache_file"]
                elif change == "po_removed":
                    self.assertEqual(meta["cache_file"], untranslated_cache)
                expected_name = {"mtime": "Constants", "po_appears": "Константы", "po_changes": "Новые константы"}.get(change, "New constants")
                self.assertEqual(current[sid][1], expected_name)
                previous = meta
        with patch.object(gamedb, "game_files", return_value=[str(base)]), patch.object(gamedb, "PO_RU", str(self.po)):
            self.assertEqual(gamedb.load_index(self.cache)[0], current)
            self.assertEqual(gamedb.fingerprint([base]), gamedb.fingerprint([base], self.po))

    def test_unit_records_skip_removed_and_unreferenced_stats(self) -> None:
        """Ловит попадание удалённых (REMOVED) записей в справочник юнитов, лишних записей STATS и потерю units в кэше."""
        base = self.write(self.root / "base.mod", _mod(
            _record("c-1", "Grunt", kind=1, data={"int": {"combat stats": 40}},
                    refs={"stats": {"st-1": [0, 0, 0]}, "race": {"r-1": [100, 0, 0]}, "dialogue": {"d-1": [0, 0, 0]}}),
            _record("c-gone", "Deleted", kind=1, data={"bool": {"REMOVED": True}}),
            _record("st-1", "1", kind=25, data={"float": {"attack": 30.0}}),
            _record("st-2", "2", kind=25, data={"float": {"attack": 99.0}}),
            _record("sq-1", "Patrol", kind=52, refs={"leader": {"c-1": [1, 0, 0]}, "AI packages": {"ai-1": [0, 0, 0]}}),
            _record("b-1", "Desert", kind=95, data={"float": {"homeless spawn amount": 2.0}},
                    refs={"homeless spawns": {"sq-1": [15, 0, 0]}}),
            _record("b-empty", "Empty", kind=95),
        ))
        units = gamedb.unit_records(gamedb.build([base], self.po))
        self.assertEqual([r["sid"] for r in units["characters"]], ["c-1"])
        self.assertEqual(set(units["characters"][0]["refs"]), {"stats", "race"})
        self.assertEqual([r["sid"] for r in units["stats"]], ["st-1"])
        self.assertEqual(set(units["squads"][0]["refs"]), {"leader"})
        self.assertEqual([(r["sid"], r["homeless spawn amount"]) for r in units["biomes"]], [("b-1", 2.0)])
        records, meta = gamedb.load_records(self.cache, [base], self.po)
        self.assertEqual(records["units"], json.loads(json.dumps(units)))
        self.assertEqual(gamedb.load_records(self.cache, [base], self.po)[1]["cache"], "hit")

    def test_race_records_follow_load_order(self) -> None:
        """Ловит иммунитет расы из базы вместо rebirth (порядок загрузки), потерю источника поля и записи в кэше."""
        base = self.write(self.root / "base.mod", _mod(
            _record("r-1", "Skel", kind=7, data={"int": {"weather immunity0": 0, "speed max skill": 100}, "float": {"bleed rate": 0.5}},
                    refs={"combat anatomy": {"head": [10, 150, 0]}}),
            _record("r-gone", "Dead", kind=7, data={"bool": {"REMOVED": True}})))
        mod = self.write(self.root / "rebirth.mod", _mod(_record(
            "r-1", "Skel", kind=7, data={"int": {"weather immunity0": 1, "weather immunity1": 4}})))
        races = gamedb.race_records(gamedb.build([base, mod], self.po))
        self.assertEqual([r["sid"] for r in races], ["r-1"])
        race = races[0]
        self.assertEqual((race["int"]["weather immunity0"], race["int"]["weather immunity1"], race["int"]["speed max skill"]),
                         (1, 4, 100))
        self.assertEqual((race["field_files"]["weather immunity0"], race["field_files"]["bleed rate"]), ("rebirth.mod", "base.mod"))
        self.assertEqual(race["weather_by_file"], {"base.mod": [0], "rebirth.mod": [1, 4]})
        self.assertEqual(gamedb.load_records(self.cache, [base, mod], self.po)[0]["races"], json.loads(json.dumps(races)))

    @unittest.skipUnless((Path(gamedb.KENSHI) / "data/gamedata.base").is_file(), "Kenshi/data/gamedata.base отсутствует")
    def test_real_game_data(self) -> None:
        base = Path(gamedb.KENSHI) / "data/gamedata.base"
        po = Path(gamedb.PO_RU)
        inputs = [base] + ([po] if po.is_file() else [])
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        raw = read_mod(base.read_bytes())["records"]
        expected_names = {r["sid"]: r["name"] for r in raw if r["name"]}
        index, meta = gamedb.load_index(self.cache, [base], po)
        self.assertEqual(meta["cache"], "rebuilt")
        self.assertEqual(set(index), {r["sid"] for r in raw})
        self.assertTrue(index)
        self.assertTrue(all(len(row) == 5 for row in index.values()))
        for sid, name in expected_names.items():
            self.assertEqual(index[sid][0], name)
        self.assertEqual(gamedb.load_index(self.cache, [base], po)[1]["cache"], "hit")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
        print(f"GameDB real: {len(index)} SID; SHA-256 входов до/после совпадают")


if __name__ == "__main__":
    unittest.main()
