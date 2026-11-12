"""Тесты нативного модуля чтения сохранений tools.save_reader."""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import unittest
from pathlib import Path

from tools.save_reader import (
    DEFAULT_PLAYER_FACTION,
    SaveReadError,
    check_save_inputs,
    extract_world_state,
    read_all_platoons,
    read_player_property,
    read_quick_save,
    read_save_snapshot,
    save_signature,
)


def _int(v: int) -> bytes:
    return struct.pack("<i", v)


def _str(v: str | bytes) -> bytes:
    raw = v.encode("utf-8") if isinstance(v, str) else v
    return _int(len(raw)) + raw


def _make_record(
    kind: int,
    sid: str,
    name: str = "",
    ints: dict[str, int] | None = None,
    floats: dict[str, float] | None = None,
    bools: dict[str, bool] | None = None,
    strings: dict[str, str] | None = None,
    refs: dict[str, list[tuple[str, list[int]]]] | None = None,
    instances: list[dict] | None = None,
) -> bytes:
    """Формирует бинарную запись GameData."""
    raw = _int(1) + _int(kind) + _int(42) + _str(name) + _str(sid) + _int(2)

    # 1. bools
    b_items = [_str(k) + (b"\x01" if v else b"\x00") for k, v in (bools or {}).items()]
    raw += _int(len(b_items)) + b"".join(b_items)

    # 2. floats
    f_items = [_str(k) + struct.pack("<f", float(v)) for k, v in (floats or {}).items()]
    raw += _int(len(f_items)) + b"".join(f_items)

    # 3. ints
    i_items = [_str(k) + _int(int(v)) for k, v in (ints or {}).items()]
    raw += _int(len(i_items)) + b"".join(i_items)

    # 4. vec3 (пусто)
    raw += _int(0)
    # 5. vec4 (пусто)
    raw += _int(0)

    # 6. strings
    s_items = [_str(k) + _str(v) for k, v in (strings or {}).items()]
    raw += _int(len(s_items)) + b"".join(s_items)

    # 7. files (пусто)
    raw += _int(0)

    # 8. refs
    r_dict = refs or {}
    raw += _int(len(r_dict))
    for cat, items in r_dict.items():
        raw += _str(cat)
        raw += _int(len(items))
        for item_sid, val_3i in items:
            raw += _str(item_sid) + struct.pack("<3i", *val_3i)

    # 9. instances
    inst_list = instances or []
    raw += _int(len(inst_list))
    for inst in inst_list:
        raw += _str(inst.get("id", "")) + _str(inst.get("target", ""))
        pos = inst.get("pos", [0.0, 0.0, 0.0])
        rot = inst.get("rot", [0.0, 0.0, 0.0, 1.0])
        raw += struct.pack("<7f", *pos, *rot)
        states = inst.get("states", [])
        raw += _int(len(states))
        for st in states:
            raw += _str(st)

    return raw


def _make_file(records: list[bytes]) -> bytes:
    """Оборачивает список бинарных записей в файл с заголовком 15."""
    header = struct.pack("<3i", 15, len(records) + 1, len(records))
    return header + b"".join(records)


class TestTownOverrides(unittest.TestCase):
    def test_extracts_override_variants_and_preserves_raw_name(self) -> None:
        """Ловит сырое raw_name из сейва, чистое name, variant после (override) и локализацию replacement_name_ru."""
        variants = [
            ("half destroyed", "half destroyed"),
            ("destroyed", "destroyed"),
            ("all ded", "all ded"),
            ("all dead", "all dead"),
            ("Deadhive", "Deadhive"),
            ("empty", "empty"),
            ("takeover", "takeover"),
            ("", ""),
        ]
        for variant_text, exp_variant in variants:
            rep_name = f"Shark (override) {variant_text}".strip()
            for name in ("Town state Shark", "Shark"):
                with self.subTest(variant=variant_text, name=name):
                    records = [{"type": 94, "name": name, "string": {
                        "replacementTown": "override-sid", "faction": "faction-sid",
                        "factions here0": "present-faction",
                    }}]
                    state = extract_world_state(records, {"override-sid": [rep_name, None]})
                    town = state["town_overrides"][0]
                    self.assertEqual(town["name"], "Shark")
                    self.assertEqual(town["raw_name"], name)
                    self.assertEqual(town["variant"], exp_variant)
                    self.assertEqual(town["replacement_name"], rep_name)
                    self.assertIsNone(town["replacement_name_ru"])
                    self.assertEqual(town["replacement_sid"], "override-sid")
                    self.assertEqual(town["faction"], "faction-sid")
                    self.assertEqual(town["factions_here"], ["present-faction"])

    def test_preserves_game_localization_when_available(self) -> None:
        """replacement_name_ru заполняется только из данных локализации игры, иначе None."""
        cases = [
            ({"override-sid": ["Shark (override) destroyed", "Разрушенная Акула"]}, "Разрушенная Акула"),
            ({"override-sid": ["Shark (override) destroyed", "Shark (override) destroyed"]}, None),
            ({"override-sid": ["Shark (override) unknown", None]}, None),
            (None, None),
        ]
        records = [
            {"type": 94, "name": "Town state Shark", "string": {"replacementTown": "override-sid"}},
            {"type": 94, "name": "Town state Без замены"},
        ]
        for gamedb, expected in cases:
            with self.subTest(gamedb=gamedb):
                state = extract_world_state(records, gamedb)
                self.assertEqual(len(state["town_overrides"]), 1)
                town = state["town_overrides"][0]
                self.assertEqual(town["name"], "Shark")
                self.assertEqual(town["raw_name"], "Town state Shark")
                self.assertEqual(town["replacement_name_ru"], expected)
                self.assertEqual(state["summary"]["total_towns"], 2)
                self.assertEqual(state["summary"]["overridden_towns_count"], 1)


class TestSaveReader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="kenshi_test_save_")
        self.save_dir = Path(self.temp_dir.name)
        self.platoon_dir = self.save_dir / "platoon"
        self.zone_dir = self.save_dir / "zone"
        self.platoon_dir.mkdir(parents=True)
        self.zone_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_synthetic_save(self) -> None:
        """Создает полное синтетическое сохранение с quick.save, squad.platoon и zone.10.10.zone."""
        # quick.save
        r56 = _make_record(
            kind=56, sid="56-state", name="State",
            ints={
                "time day": 15, "time hour": 10, "time minute": 45,
                "player money": 25000, "members": 1, "squads": 1,
                "usedUniquesState0": 2,
            },
            strings={"usedUniques0": "leader-holy-phoenix"},
            bools={"usedUniquesPlayer0": True},
            refs={"mods": [("gamedata.base", [0, 0, 0]), ("test_mod.mod", [0, 0, 0])]},
        )
        r34 = _make_record(
            kind=34, sid="34-squad", name="Alpha Squad",
            ints={"squad index": 0},
            bools={"dead": False},
            strings={"platoon stringID": "squad_alpha", "faction stringID": DEFAULT_PLAYER_FACTION, "faction name": "Nameless"},
        )
        r21 = _make_record(
            kind=21, sid="21-research", name="Research",
            floats={"num finished": 1.0, "num currents": 0.0},
            strings={"finished0": "tech_swords"},
        )
        r37 = _make_record(
            kind=37, sid="37-diplo", name="Faction relations",
            floats={"relation0": 55.0, "trust0": 10.0, "trustNeg0": 0.0},
            strings={"relationSID0": "shek-faction"},
        )
        r94 = _make_record(
            kind=94, sid="94-town", name="Town state The Hub",
            ints={"handTYPE": 11, "handC": 1, "handS": 2, "handI": 3, "handCS": 4},
            strings={"faction": "neutral", "factions here0": DEFAULT_PLAYER_FACTION, "replacementTown": ""},
        )
        quick_bytes = _make_file([r56, r34, r21, r37, r94])
        (self.save_dir / "quick.save").write_bytes(quick_bytes)

        # platoon/squad_alpha.platoon
        r_item = _make_record(
            kind=42, sid="item-katana", name="Katana",
            ints={"quantity": 1, "level": 80, "inventory x": 0, "inventory y": 0},
            strings={"section": "hip", "base data sid": "katana_base"},
        )
        r_prosth_item = _make_record(
            kind=42, sid="item-prosthetic-arm", name="KLR Arm",
            ints={"level": 95},
            strings={"base data sid": "klr_arm_base"},
        )
        r_cont = _make_record(
            kind=41, sid="cont-inv", name="Inventory",
            instances=[{"id": "inst-it-1", "target": "item-katana"}],
        )
        r_state = _make_record(
            kind=36, sid="char-st", name="0",
            ints={"portrait_serial": 777888, "carryingS": 0},
            strings={"name": "Хот Лон", "owner faction ID": DEFAULT_PLAYER_FACTION},
        )
        r_stats = _make_record(
            kind=25, sid="char-stats", name="Хот Лон",
            floats={"strength": 40.5, "dexterity": 55.0, "attack": 38.0},
        )
        # limbs: биты 0..1 для left_arm = 2 (протез), биты 2..3 для right_arm = 1 (потеряна)
        # limbs = (2 << 0) | (1 << 2) = 2 | 4 = 6
        r_med = _make_record(
            kind=57, sid="char-med", name="Medical",
            ints={"limbs": 6},
            floats={"blood": 100.0, "hung": 2.8, "flesh0": 100.0, "wear0": 0.0, "flesh1": 0.0},
            strings={"sid0": "left arm", "sid1": "right arm"},
            refs={"limbs": [("item-prosthetic-arm", [0, 0, 0])]},
        )
        r_job = _make_record(
            kind=67, sid="char-jobs", name="Jobs",
            strings={"pjob0": "guard_job"},
        )
        r_look = _make_record(
            kind=66, sid="char-look", name="Appearance",
            bools={"sex female": False},
            ints={"Age": 30},
            refs={"race": [("greenlander", [0, 0, 0])]},
        )
        r30 = _make_record(
            kind=30, sid="platoon-root", name="Platoon",
            instances=[{
                "id": "char-inst-1",
                "target": "greenlander",
                "pos": [10.0, 20.0, 30.0],
                "rot": [0.0, 0.0, 0.0, 1.0],
                "states": ["char-st", "char-stats", "char-med", "cont-inv", "char-jobs", "char-look"],
            }],
        )
        platoon_bytes = _make_file([r30, r_state, r_stats, r_med, r_cont, r_item, r_prosth_item, r_job, r_look])
        (self.platoon_dir / "squad_alpha.platoon").write_bytes(platoon_bytes)

        # zone/zone.10.10.zone
        r_storage_item = _make_record(
            kind=42, sid="item-iron", name="Iron",
            ints={"quantity": 25},
            strings={"base data sid": "iron_ore"},
        )
        r_box = _make_record(
            kind=41, sid="cont-box", name="Chest",
            instances=[{"id": "inst-iron", "target": "item-iron"}],
        )
        # Внешнее здание (parent)
        r_bldg_parent = _make_record(
            kind=35, sid="bldg-house", name="Storm House",
            ints={"handleTYPE": 11, "handleC": 10, "handleS": 20, "handleI": 30, "handleCS": 40},
            bools={"is inside building": False, "is complete": True},
            strings={"owner faction ID": DEFAULT_PLAYER_FACTION},
        )
        # Объект интерьера (child: станок/сундук внутри родительского здания)
        r_bldg_child = _make_record(
            kind=35, sid="bldg-chest", name="Iron Chest",
            ints={
                "handleTYPE": 11, "handleC": 10, "handleS": 20, "handleI": 31, "handleCS": 41,
                "isInsideBuildingTYPE": 11, "isInsideBuildingC": 10, "isInsideBuildingS": 20,
                "isInsideBuildingI": 30, "isInsideBuildingCS": 40,
                "floornum": 1,
            },
            bools={"is inside building": True, "is complete": True},
            strings={"owner faction ID": DEFAULT_PLAYER_FACTION},
        )
        # Запись 30 с инстансами зданий
        r30_zone = _make_record(
            kind=30, sid="0-buildinglist", name="Buildings",
            instances=[
                {
                    "id": "inst-house-1",
                    "target": "storm_house_tpl",
                    "pos": [100.0, 200.0, 0.0],
                    "states": ["bldg-house"],
                },
                {
                    "id": "inst-chest-1",
                    "target": "chest_tpl",
                    "pos": [105.0, 205.0, 2.0],
                    "states": ["bldg-chest", "cont-box"],
                },
            ],
        )
        zone_bytes = _make_file([r30_zone, r_bldg_parent, r_bldg_child, r_box, r_storage_item])
        (self.zone_dir / "zone.10.10.zone").write_bytes(zone_bytes)

    def test_quick_save_parsing(self) -> None:
        self._build_synthetic_save()
        data = read_quick_save(self.save_dir)

        # Мир и счетчики
        self.assertEqual(data["world"]["money"], 25000)
        self.assertEqual(data["world"]["day"], 15)
        self.assertEqual(data["player_counts"]["members"], 1)
        self.assertEqual(data["mods"], ["gamedata.base", "test_mod.mod"])

        # Реестр
        self.assertIn("squad_alpha", data["registry"])
        self.assertEqual(data["registry"]["squad_alpha"]["name"], "Alpha Squad")
        self.assertEqual(data["registry"]["squad_alpha"]["faction_sid"], DEFAULT_PLAYER_FACTION)

        # Исследования
        self.assertEqual(data["research"]["finished"], ["tech_swords"])

        # Дипломатия
        self.assertEqual(len(data["diplomacy_raw"]), 1)
        self.assertIn("shek-faction", data["diplomacy_raw"][0]["relations"])
        self.assertEqual(data["diplomacy_raw"][0]["relations"]["shek-faction"]["relation"], 55.0)

        # Уникальные NPC (без домысливания)
        self.assertEqual(len(data["uniques_raw"]), 1)
        self.assertEqual(data["uniques_raw"][0]["sid"], "leader-holy-phoenix")
        self.assertEqual(data["uniques_raw"][0]["state_code"], 2)
        self.assertIs(data["uniques_raw"][0]["player"], True)

        # Города
        self.assertEqual(len(data["towns"]), 1)
        self.assertEqual(data["towns"][0]["name"], "The Hub")

    def test_platoon_and_character_extraction(self) -> None:
        self._build_synthetic_save()
        squads, chars, warnings = read_all_platoons(self.save_dir, DEFAULT_PLAYER_FACTION)

        self.assertEqual(len(squads), 1)
        self.assertEqual(squads[0]["squad_id"], "squad_alpha")
        self.assertEqual(squads[0]["members"], ["Хот Лон"])

        self.assertEqual(len(chars), 1)
        c = chars[0]
        # Проверка стабильного ID
        self.assertEqual(c["id"], "ps:777888")
        self.assertEqual(c["name"], "Хот Лон")
        self.assertEqual(c["attributes_base"]["Strength"], 40.5)
        self.assertEqual(c["attributes_base"]["Dexterity"], 55.0)
        self.assertEqual(c["skills_base"]["Melee Attack"], 38.0)
        self.assertEqual(c["health"]["blood"], 100.0)

        # Протезы и потерянные конечности
        self.assertIn("left_arm", c["prosthetics"])
        self.assertEqual(c["prosthetics"]["left_arm"]["grade"], "Мастерский")
        self.assertIn("right_arm", c["lost_limbs"])

        # Отметки в health.parts
        parts = {p["part_sid"]: p for p in c["health"]["parts"]}
        self.assertEqual(parts["left arm"].get("limb"), "prosthetic")
        self.assertEqual(parts["right arm"].get("limb"), "lost")

        # Экипировка и инвентарь
        self.assertEqual(len(c["weapons"]), 1)
        self.assertEqual(c["weapons"][0]["section"], "hip")

    def test_player_property_hierarchy(self) -> None:
        self._build_synthetic_save()
        prop = read_player_property(self.save_dir, DEFAULT_PLAYER_FACTION)

        # Верхнеуровневых построек ровно 1 (Storm House)
        self.assertEqual(len(prop["buildings"]), 1)
        parent = prop["buildings"][0]
        self.assertEqual(parent["id"], "inst-house-1")
        self.assertEqual(parent["name"], "Storm House")
        self.assertEqual(parent["zone"], "10.10")
        self.assertEqual(parent["position"], [100.0, 200.0, 0.0])

        # Объект интерьера вложен в родительское здание
        self.assertEqual(len(parent["interior"]), 1)
        child = parent["interior"][0]
        self.assertEqual(child["id"], "inst-chest-1")
        self.assertEqual(child["name"], "Iron Chest")
        self.assertEqual(child["floor"], 1)

        # Хранилище интерьера содержит предметы
        self.assertEqual(len(child["storage_items"]), 1)
        self.assertEqual(child["storage_items"][0]["base_data_sid"], "iron_ore")
        self.assertEqual(child["storage_items"][0]["quantity"], 25)

    def test_signature_and_integrity_validation(self) -> None:
        self._build_synthetic_save()

        # Сигнатура
        sig = save_signature(self.save_dir)
        self.assertTrue(len(sig) > 0)

        # Валидный сейв
        check_save_inputs(self.save_dir, DEFAULT_PLAYER_FACTION)

        # 1. Отсутствует quick.save
        (self.save_dir / "quick.save").unlink()
        with self.assertRaises(SaveReadError):
            check_save_inputs(self.save_dir, DEFAULT_PLAYER_FACTION)

        # 2. Поврежденный заголовок quick.save
        (self.save_dir / "quick.save").write_bytes(b"corrupt_header_bytes")
        with self.assertRaises(SaveReadError):
            check_save_inputs(self.save_dir, DEFAULT_PLAYER_FACTION)

        # 3. Отсутствует обязательный .platoon игрока
        self._build_synthetic_save()
        (self.platoon_dir / "squad_alpha.platoon").unlink()
        with self.assertRaises(SaveReadError) as ctx:
            check_save_inputs(self.save_dir, DEFAULT_PLAYER_FACTION)
        self.assertIn("squad_alpha.platoon", str(ctx.exception))

    def test_full_save_snapshot(self) -> None:
        self._build_synthetic_save()
        snap = read_save_snapshot(self.save_dir, DEFAULT_PLAYER_FACTION)

        self.assertEqual(snap["save"]["name"], self.save_dir.name)
        # Путь не раскрывает абсолютный каталог
        self.assertEqual(snap["save"]["path"], self.save_dir.name)
        self.assertEqual(len(snap["characters"]), 1)
        self.assertEqual(snap["characters"][0]["id"], "ps:777888")
        self.assertEqual(len(snap["property"]["buildings"]), 1)
        self.assertEqual(snap["world"]["money"], 25000)

    def test_real_save_read_only_integration(self) -> None:
        """Интеграционный тест на реальном сохранении: сверка с player_counts и контроль SHA-256."""
        local = os.environ.get("LOCALAPPDATA")
        candidates = [
            Path(local) / "kenshi/save/autosave0" if local else Path("non_existent"),
            Path(local) / "kenshi/save/hot lon" if local else Path("non_existent"),
            Path(local) / "kenshi/save/quicksave" if local else Path("non_existent"),
        ]
        real_save = next((p for p in candidates if (p / "quick.save").is_file()), None)
        if not real_save:
            self.skipTest("Реальное сохранение Kenshi не найдено на хосте")

        # Собираем SHA-256 ВСЕХ файлов сохранения до чтения
        files_to_verify = [real_save / "quick.save"]
        pdir = real_save / "platoon"
        if pdir.is_dir():
            files_to_verify.extend(list(pdir.glob("*.platoon")))
        zdir = real_save / "zone"
        if zdir.is_dir():
            files_to_verify.extend(list(zdir.glob("*.zone")))

        hashes_before = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files_to_verify}

        # Выполняем чтение снимка
        snap = read_save_snapshot(real_save, DEFAULT_PLAYER_FACTION)

        # Сверка со счетчиками quick.save (members и squads)
        expected_members = snap["player_counts"].get("members")
        expected_squads = snap["player_counts"].get("squads")
        if expected_members is not None:
            self.assertEqual(len(snap["characters"]), expected_members, f"Количество персонажей не совпало с members={expected_members}")
        if expected_squads is not None:
            self.assertEqual(len(snap["squads"]), expected_squads, f"Количество отрядов не совпало с squads={expected_squads}")

        for ch in snap["characters"]:
            self.assertTrue(ch["id"].startswith("ps:"), f"Character ID must start with ps:, got {ch['id']}")
            self.assertIsInstance(ch["name"], str)

        # Сверяем хеши файлов после чтения
        hashes_after = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files_to_verify}
        self.assertEqual(hashes_before, hashes_after, "Исходные файлы сохранения изменились при чтении!")


if __name__ == "__main__":
    unittest.main()
