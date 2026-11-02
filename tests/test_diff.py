"""Тесты AC-04 и AC-05: детерминированный diff по снимкам, разделение по ID, transfers, baseline/non-baseline."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Any

from tools.diff import COMPATIBLE, DIFF_FORMAT, diff, make_diff_id, store_diff
from tools.io_utils import canonical, read_json

ROOT = Path(__file__).resolve().parents[1]


def _make_snapshot(
    characters: list[dict[str, Any]],
    world: dict[str, Any] | None = None,
    squads: list[dict[str, Any]] | None = None,
    supplies: dict[str, Any] | None = None,
    save: dict[str, Any] | None = None,
    fmt: str = "kenshi-session/10",
) -> dict[str, Any]:
    return {
        "format": fmt,
        "save": save or {"name": "test_save", "kind": "manual", "saved_at": "2026-11-01T12:00:00"},
        "world": world or {"day": 10, "time": "12:00", "money": 5000},
        "characters": characters,
        "squads": squads or [{"squad_id": "sq_1", "squad_name": "Основной отряд", "members": [c.get("name") for c in characters]}],
        "supplies": supplies or {"medkits": {"total": 5, "nutrition": 0}},
        "warnings": [],
        "meta": {"session_file": "test_session.json"},
    }


class DiffTests(unittest.TestCase):
    def test_ac04_same_name_characters_split_by_id_and_transfers(self) -> None:
        """AC-04: Табличная fixture с двумя одноимёнными участниками:

        - раздельный учет изменений по ID;
        - transfers по ID и sid (метки не склеивают участников);
        - инвариантность к перестановке входного порядка;
        - запрет склеивания разных ps:* по имени.
        """
        c1_prev = {
            "id": "ps:101",
            "name": "Вьючный бык",
            "squad_id": "sq_1",
            "status": {"dead": False, "unconscious": False},
            "attributes_base": {"Strength": 20.0},
            "skills_base": {"Athletics": 15.0},
            "inventory": [{"name": "Аптечка", "sid": "1359-gamedata.base", "quantity": 4}],
            "backpack": {"contents": []},
        }
        c2_prev = {
            "id": "ps:102",
            "name": "Вьючный бык",
            "squad_id": "sq_1",
            "status": {"dead": False, "unconscious": False},
            "attributes_base": {"Strength": 10.0},
            "skills_base": {"Athletics": 5.0},
            "inventory": [{"name": "Аптечка", "sid": "1359-gamedata.base", "quantity": 1}],
            "backpack": {"contents": []},
        }

        # В cur: c1 отдал 2 аптечки c2 (у c1 стало 2, у c2 стало 3).
        # c1 прокачал Strength (20 -> 25), c2 прокачал Athletics (5 -> 10).
        c1_cur = copy.deepcopy(c1_prev)
        c1_cur["attributes_base"]["Strength"] = 25.0
        c1_cur["inventory"][0]["quantity"] = 2

        c2_cur = copy.deepcopy(c2_prev)
        c2_cur["skills_base"]["Athletics"] = 10.0
        c2_cur["inventory"][0]["quantity"] = 3

        snap_prev = _make_snapshot([c1_prev, c2_prev])
        snap_cur = _make_snapshot([c1_cur, c2_cur])

        d = diff(snap_prev, snap_cur, prev_id="snap-1", cur_id="snap-2")
        self.assertTrue(d["valid"])

        # 1. Раздельный учет изменений навыков по ID
        skills_by_id = {s["id"]: s for s in d["skills"]}
        self.assertIn("ps:101", skills_by_id)
        self.assertIn("ps:102", skills_by_id)
        self.assertEqual(skills_by_id["ps:101"]["name"], "Вьючный бык")
        self.assertEqual(skills_by_id["ps:102"]["name"], "Вьючный бык")
        self.assertEqual(skills_by_id["ps:101"]["changes"]["Strength"]["delta"], 5.0)
        self.assertEqual(skills_by_id["ps:102"]["changes"]["Athletics"]["delta"], 5.0)

        # 2. Передача предметов по ID и sid
        self.assertEqual(len(d["transfers"]), 1)
        transfer = d["transfers"][0]
        self.assertEqual(transfer["sid"], "1359-gamedata.base")
        self.assertEqual(transfer["party_net"], 0)
        # Участники помечены метками с ID (без склеивания в один ключ!)
        self.assertIn("Вьючный бык [ps:101]", transfer["from"])
        self.assertEqual(transfer["from"]["Вьючный бык [ps:101]"], -2)
        self.assertIn("Вьючный бык [ps:102]", transfer["to"])
        self.assertEqual(transfer["to"]["Вьючный бык [ps:102]"], 2)

        # 3. Инвариантность к перестановке входного порядка
        snap_cur_permuted = _make_snapshot([c2_cur, c1_cur])
        d_permuted = diff(snap_prev, snap_cur_permuted, prev_id="snap-1", cur_id="snap-2")
        self.assertEqual(canonical(d), canonical(d_permuted))

        # 4. Запрет склеивания разных ps:* по имени
        # Заменим c2 на c3 с тем же именем, но другим ID "ps:103"
        c3_cur = copy.deepcopy(c2_cur)
        c3_cur["id"] = "ps:103"
        snap_cur_different_id = _make_snapshot([c1_cur, c3_cur])
        d_diff_id = diff(snap_prev, snap_cur_different_id, prev_id="snap-1", cur_id="snap-2")
        self.assertTrue(d_diff_id["valid"])
        # ps:102 должен быть в left, ps:103 должен быть в joined; они НЕ должны склеиться в id_changed!
        left_ids = [c["id"] for c in d_diff_id["roster"]["left"]]
        joined_ids = [c["id"] for c in d_diff_id["roster"]["joined"]]
        self.assertIn("ps:102", left_ids)
        self.assertIn("ps:103", joined_ids)
        self.assertNotIn("id_changed", d_diff_id["roster"])

    def test_ac05_numeric_diff_and_baseline_modes(self) -> None:
        """AC-05: рабочий числовой diff, baseline / non-baseline режимы, запрет ложных нулей изменений."""
        c_prev = {
            "id": "ps:1",
            "name": "Хот Лон",
            "squad_id": "sq_1",
            "status": {"dead": False, "unconscious": False, "coma": False},
            "attributes_base": {"Strength": 30.0, "Dexterity": 20.0},
            "skills_base": {"Melee Attack": 40.0},
            "condition": {"blood": 100.0, "hunger_raw": 2.5},
            "health": [
                {"part": "Грудь", "flesh": 100.0, "bandage": 0.0, "wear": 0.0, "stun": 0.0},
                {"part": "Голова", "flesh": 100.0, "bandage": 0.0, "wear": 0.0, "stun": 0.0},
            ],
            "weapons": [{"slot": "weapon_back", "name": "Катана", "sid": "w_1", "grade": "MK I", "level": 50}],
            "armour": {"chest": {"name": "Латы", "sid": "a_1", "level": 40}},
            "backpack": {"sid": "bp_1", "name": "Рюкзак вора"},
            "belt": {"sid": "belt_1", "name": "Фонарь"},
            "lost_limbs": [],
            "prosthetics": [],
            "inventory": [{"name": "Бинт", "sid": "item_1", "quantity": 3}],
        }

        # 1. Non-baseline режим (prev = None)
        cur_snap = _make_snapshot([c_prev], world={"day": 10, "time": "12:00", "money": 1000})
        d_none = diff(None, cur_snap, cur_id="snap-initial")
        self.assertFalse(d_none["valid"])
        self.assertIsNone(d_none["from"])
        self.assertEqual(d_none["to"]["snapshot_id"], "snap-initial")
        self.assertIn("Предыдущий снимок (baseline) не задан", d_none["problems"])
        self.assertTrue(len(d_none["not_compared"]) >= 3)
        self.assertEqual(d_none["summary"], {})
        self.assertEqual(d_none["compared"], {})

        # 2. Числовой diff в baseline режиме
        c_cur = copy.deepcopy(c_prev)
        c_cur["attributes_base"]["Strength"] = 35.5
        c_cur["condition"]["blood"] = 85.0
        c_cur["health"][0]["flesh"] = 70.0
        c_cur["health"][0]["bandage"] = 30.0
        c_cur["armour"]["chest"]["level"] = 60
        c_cur["status"]["coma"] = True

        cur_snap2 = _make_snapshot(
            [c_cur],
            world={"day": 11, "time": "14:00", "money": 3500},
            supplies={"medkits": {"total": 4, "nutrition": 0}},
        )

        d = diff(cur_snap, cur_snap2, prev_id="snap-1", cur_id="snap-2")
        self.assertTrue(d["valid"])
        self.assertEqual(d["from"]["snapshot_id"], "snap-1")
        self.assertEqual(d["to"]["snapshot_id"], "snap-2")

        # Проверка числовых изменений
        self.assertEqual(d["world"]["money"]["delta"], 2500)
        self.assertEqual(d["world"]["day"]["delta"], 1)
        self.assertEqual(d["skills"][0]["changes"]["Strength"]["delta"], 5.5)
        self.assertEqual(d["health"][0]["condition"]["blood"]["delta"], -15.0)
        self.assertEqual(d["health"][0]["parts"]["Грудь"]["flesh"]["delta"], -30.0)
        self.assertEqual(d["health"][0]["parts"]["Грудь"]["bandage"]["delta"], 30.0)
        self.assertEqual(d["equipment"][0]["changes"]["chest"]["to"]["level"], 60)
        self.assertEqual(d["roster"]["status_changed"][0]["field"], "coma")
        self.assertEqual(d["roster"]["status_changed"][0]["to"], True)

        # 3. Неверный родитель (mismatch parent_snapshot_id)
        d_bad_parent = diff(cur_snap, cur_snap2, prev_id="snap-1", cur_id="snap-2", parent_snapshot_id="snap-other")
        self.assertFalse(d_bad_parent["valid"])
        self.assertTrue(any("несовпадение родителя" in p for p in d_bad_parent["problems"]))

        # 4. Убывание игрового времени (откат / ветвление)
        cur_snap_retro = copy.deepcopy(cur_snap2)
        cur_snap_retro["world"]["day"] = 9
        d_retro = diff(cur_snap, cur_snap_retro, prev_id="snap-1", cur_id="snap-retro")
        self.assertFalse(d_retro["valid"])
        self.assertTrue(any("игровой день уменьшился" in p for p in d_retro["problems"]))

        # 5. Разные игры (нет общих ID)
        c_alien = copy.deepcopy(c_prev)
        c_alien["id"] = "ps:9999"
        snap_alien = _make_snapshot([c_alien])
        d_alien = diff(cur_snap, snap_alien, prev_id="snap-1", cur_id="snap-alien")
        self.assertFalse(d_alien["valid"])
        self.assertTrue(any("ни одного общего id" in p for p in d_alien["problems"]))

        # 6. Запрет ложных нулей: пустой diff без сравниваемых сущностей
        empty_snap1 = _make_snapshot([], world={})
        empty_snap2 = _make_snapshot([], world={})
        d_empty = diff(empty_snap1, empty_snap2, prev_id="s1", cur_id="s2")
        self.assertFalse(d_empty["valid"])

        # 7. Детерминированное сохранение diff
        import tempfile
        diff_id = make_diff_id("snap-1", "snap-2")
        with tempfile.TemporaryDirectory() as tmp_str:
            out_file = store_diff(Path(tmp_str), diff_id, canonical(d))
            self.assertTrue(out_file.is_file())
            reloaded = read_json(out_file.read_bytes())
            self.assertEqual(canonical(d), canonical(reloaded))

    def test_v11_diff_diplomacy_and_world_state(self) -> None:
        """Сравнение дипломатии и мирового состояния для kenshi-session/11."""
        c = {"id": "ps:1", "name": "Хоббс", "squad_id": "sq_1", "jobs": {"enabled": True, "primary": []}}
        snap1 = _make_snapshot([c], fmt="kenshi-session/11")
        snap1["diplomacy"] = {
            "player_faction_sid": "204-base",
            "relations": [
                {"faction_sid": "Flotsam", "faction_name": "Ниндзя", "relation": 50.0, "standing": "neutral"},
                {"faction_sid": "HolyNation", "faction_name": "Святая Нация", "relation": -50.0, "standing": "unfriendly"},
            ],
            "summary": {},
        }
        snap1["world_state"] = {
            "unique_npcs": [
                {"sid": "phoenix", "name": "Lord Phoenix", "name_ru": "Лорд Феникс", "state_code": 1, "state": None},
                {"sid": "esata", "name": "Esata", "name_ru": "Эсата", "state_code": 1, "state": None},
            ],
            "town_overrides": [],
            "summary": {},
        }

        snap2 = copy.deepcopy(snap1)
        snap2["world"]["day"] = 11
        # Flotsam: relation 50 -> 80 (+30), standing neutral -> ally
        snap2["diplomacy"]["relations"][0]["relation"] = 80.0
        snap2["diplomacy"]["relations"][0]["standing"] = "ally"
        # Phoenix: state_code 1 -> 2, state None -> "dead"
        snap2["world_state"]["unique_npcs"][0]["state_code"] = 2
        snap2["world_state"]["unique_npcs"][0]["state"] = "dead"
        # Bad Teeth: town override by Shek Kingdom
        snap2["world_state"]["town_overrides"].append({
            "replacement_sid": "rep_bad_teeth",
            "name": "Плохие Зубы",
            "faction": "Shek Kingdom",
        })

        d = diff(snap1, snap2, prev_id="s1", cur_id="s2")
        self.assertTrue(d["valid"])
        self.assertIn("diplomacy", d)
        self.assertEqual(len(d["diplomacy"]), 1)
        self.assertEqual(d["diplomacy"][0]["faction_sid"], "Flotsam")
        self.assertEqual(d["diplomacy"][0]["relation"]["delta"], 30.0)
        self.assertEqual(d["diplomacy"][0]["standing"]["to"], "ally")

        self.assertIn("world_state", d)
        self.assertEqual(len(d["world_state"]["unique_npcs"]), 1)
        self.assertEqual(d["world_state"]["unique_npcs"][0]["sid"], "phoenix")
        self.assertEqual(d["world_state"]["unique_npcs"][0]["state_code"]["to"], 2)
        self.assertEqual(d["world_state"]["unique_npcs"][0]["state"]["to"], "dead")

        self.assertEqual(len(d["world_state"]["town_overrides"]), 1)
        self.assertEqual(d["world_state"]["town_overrides"][0]["status"], "overridden")
        self.assertEqual(d["world_state"]["town_overrides"][0]["town_name"], "Плохие Зубы")

        self.assertEqual(d["summary"]["diplomacy_changes"], 1)
        self.assertEqual(d["summary"]["world_state_changes"], 2)

    def test_v11_diff_character_jobs_and_base_storage(self) -> None:
        """Сравнение очереди работ персонажей и складских запасов баз."""
        c1_prev = {
            "id": "ps:1",
            "name": "Хоббс",
            "squad_id": "sq_1",
            "jobs": {
                "enabled": False,
                "primary": [{"index": 0, "task_code": 14, "task_name": "Лечить"}],
            },
        }
        snap1 = _make_snapshot([c1_prev], fmt="kenshi-session/11")
        snap1["property"] = {
            "buildings": [
                {
                    "id": "bld_1",
                    "name": "Сундук",
                    "zone": "zone_1",
                    "storage": [{"sid": "iron_ore", "name": "Железная руда", "quantity": 10}],
                }
            ]
        }

        snap2 = copy.deepcopy(snap1)
        snap2["world"]["day"] = 11
        # c1 jobs: enabled False -> True, add bodyguard task
        snap2["characters"][0]["jobs"]["enabled"] = True
        snap2["characters"][0]["jobs"]["primary"].append({"index": 1, "task_code": 11, "task_name": "Телохранитель"})
        # base storage: iron_ore 10 -> 50 (+40)
        snap2["property"]["buildings"][0]["storage"][0]["quantity"] = 50

        d = diff(snap1, snap2, prev_id="s1", cur_id="s2")
        self.assertTrue(d["valid"])
        self.assertIn("jobs", d)
        self.assertEqual(len(d["jobs"]), 1)
        self.assertEqual(d["jobs"][0]["id"], "ps:1")
        self.assertEqual(d["jobs"][0]["changes"]["enabled"]["to"], True)
        self.assertEqual(len(d["jobs"][0]["changes"]["primary"]["to"]), 2)

        self.assertIn("base_storage", d)
        self.assertEqual(len(d["base_storage"]), 1)
        self.assertEqual(d["base_storage"][0]["building_id"], "bld_1")
        self.assertEqual(d["base_storage"][0]["sid"], "iron_ore")
        self.assertEqual(d["base_storage"][0]["delta"], 40)

        self.assertEqual(d["summary"]["job_changes"], 1)
        self.assertEqual(d["summary"]["base_storage_changes"], 1)

    def test_v10_to_v11_cross_version_compatibility(self) -> None:
        """Сравнение v10 с v11: новые категории не вызывают ошибок и уходят в not_compared."""
        c = {"id": "ps:1", "name": "Хоббс", "squad_id": "sq_1"}
        snap_v10 = _make_snapshot([c], fmt="kenshi-session/10")
        snap_v11 = _make_snapshot([c], fmt="kenshi-session/11")
        snap_v11["world"]["day"] = 11

        d = diff(snap_v10, snap_v11, prev_id="s_v10", cur_id="s_v11")
        self.assertTrue(d["valid"])
        # Новые ключи не должны присутствовать в верхнем уровне diff
        self.assertNotIn("diplomacy", d)
        self.assertNotIn("world_state", d)
        self.assertNotIn("jobs", d)
        self.assertNotIn("base_storage", d)
        # В not_compared должно быть явное указание на версию
        self.assertTrue(any("дипломатия, состояние мира" in nc for nc in d["not_compared"]))

    def test_v11_diff_determinism(self) -> None:
        """Детерминированность: повторный запуск diff на v11 дает побайтово идентичный результат."""
        c = {
            "id": "ps:1",
            "name": "Хоббс",
            "squad_id": "sq_1",
            "jobs": {"enabled": True, "primary": [{"index": 0, "task_code": 14, "task_name": "Лечить"}]},
        }
        snap1 = _make_snapshot([c], fmt="kenshi-session/11")
        snap1["diplomacy"] = {"relations": [{"faction_sid": "f1", "relation": 10.0, "standing": "neutral"}], "summary": {}}
        snap1["world_state"] = {"unique_npcs": [{"sid": "u1", "state_code": 1}], "town_overrides": [], "summary": {}}
        snap1["property"] = {"buildings": [{"id": "b1", "storage": [{"sid": "s1", "quantity": 5}]}]}

        snap2 = copy.deepcopy(snap1)
        snap2["world"]["day"] = 12
        snap2["diplomacy"]["relations"][0]["relation"] = 50.0
        snap2["diplomacy"]["relations"][0]["standing"] = "friendly"
        snap2["world_state"]["unique_npcs"][0]["state_code"] = 2
        snap2["property"]["buildings"][0]["storage"][0]["quantity"] = 20

        d1 = diff(snap1, snap2, prev_id="s1", cur_id="s2")
        d2 = diff(snap1, snap2, prev_id="s1", cur_id="s2")
        self.assertEqual(canonical(d1), canonical(d2))
