"""Тесты декодирования очереди работ (jobs), переноски (carrying), наград и боевых режимов (Этап 2.2)."""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from tools.config import Config
from tools.io_utils import read_json, tree_hashes
from tools.pipeline import run_pipeline
from tools.rendering import render_brief
from tests.test_save_reader import _make_file, _make_record
from tools.save_reader import (
    clean_handle,
    clean_job_handle,
    decode_jobs,
    parse_bounties,
    parse_character,
    read_all_platoons,
)
from tools.task_types import TASK_TYPE_NAMES, get_task_name


class TestCharacterJobsAndCarryingSynthetic(unittest.TestCase):
    """Синтетические тесты декодирования работ, переноски и боевых режимов."""

    def test_native_extraction_preserves_identity_and_extended_fields(self) -> None:
        # Один отряд чужой фракции содержит двух одноимённых персонажей игрока;
        # второй персонаж имеет только запасной handle вместо portrait_serial.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game, save = root / "game", root / "saves/one"
            (game / "data").mkdir(parents=True)
            (save / "platoon").mkdir(parents=True)
            for name in ("gamedata.base", "Newwworld.mod", "Dialogue.mod", "rebirth.mod"):
                (game / "data" / name).write_bytes(struct.pack("<iiii", 17, 0, 0, 0))
            faction = "204-gamedata.base"
            (save / "quick.save").write_bytes(_make_file([
                _make_record(56, "world", ints={"time day": 2, "members": 2, "squads": 1,
                                              "usedUniquesState0": 2}, strings={"usedUniques0": "npc"}),
                _make_record(34, "registry", strings={"platoon stringID": "a", "faction stringID": "other"}),
                _make_record(37, "player", strings={"gamedata stringID": faction}),
                _make_record(37, "other", strings={"gamedata stringID": "other", "relationSID0": "player"},
                             floats={"relation0": 55.0}),
            ]))
            records, instances = [], []
            for n, identity in enumerate(({"portrait_serial": 777}, {"handleTYPE": 1, "handleC": 2,
                                                                     "handleS": 3, "handleI": 4, "handleCS": 5})):
                state, job = f"state{n}", f"jobs{n}"
                records.extend([
                    _make_record(36, state, strings={"name": "Одинаковое имя", "owner faction ID": faction,
                                                     "bountyfac0": "other"},
                                 ints=identity | {"bountyexp0": 200 + n, "carryingTYPE": 1, "carryingC": 4},
                                 bools={"carrying": n == 0, "ranged mode": True}),
                    _make_record(67, job, ints={"pjob0": 58 + n}, bools={"jobs": True}),
                ])
                instances.append({"id": f"instance{n}", "target": "human", "states": [state, job]})
            records.append(_make_record(30, "platoon", instances=instances))
            (save / "platoon/a.platoon").write_bytes(_make_file(records))
            before = tree_hashes(save)
            config = Config((root / "saves",), game, root / "workshop", faction)
            output = root / "snapshots"
            published = root / "published"
            pipeline_args = dict(source=save, source_kind="save", config=config, output_snapshots=output,
                                 output_diffs=root / "diffs", published_dir=published, world_dir=root / "world",
                                 index_path=root / "index.json", campaign_id="fixture",
                                 sessions_root=root / "sessions")
            result = run_pipeline(**pipeline_args)
            self.assertEqual(result["pipeline_status"], "success")
            current = read_json((output / result["snapshot_id"] / "current.json").read_bytes())
            provenance = read_json((output / result["snapshot_id"] / "provenance.json").read_bytes())
            self.assertEqual(current["format"], "kenshi-session/11")
            self.assertEqual(provenance["source_format"], current["format"])
            manifest = read_json((published / "manifest.json").read_bytes())
            self.assertEqual(manifest["source_format"], current["format"])
            self.assertEqual(manifest["validation"]["status"], "passed")
            schema = read_json((Path(__file__).resolve().parents[1] / "contracts/session.schema.json").read_bytes())
            Draft202012Validator(schema).validate(current)
            chars = {c["id"]: c for c in current["characters"]}
            self.assertEqual(set(chars), {"ps:777", "h:1:2:3:4:5"})
            for n, cid in enumerate(("ps:777", "h:1:2:3:4:5")):
                ch = chars[cid]
                self.assertEqual(ch["jobs"]["primary"][0]["task_code"], 58 + n)
                self.assertEqual(ch["bounties"]["total_amount"], 200 + n)
                self.assertTrue(ch["combat_modes"]["ranged"])
                self.assertEqual(ch["carrying"]["is_carrying"], n == 0)
                self.assertIsInstance(ch["health"], list)
                self.assertIn("inventory", ch)
            self.assertNotIn("standing", current["diplomacy"]["relations"][0])
            self.assertEqual(current["world_state"]["unique_npcs"][0]["state_code"], 2)
            self.assertIn("supplies", current)
            # Регрессия: удалённая аналитика не возвращается ни в снимок, ни в публикацию
            texts = {str(f.relative_to(published)): f.read_text(encoding="utf-8")
                     for f in published.rglob("*") if f.suffix in (".json", ".md", ".jsonl")}
            texts["current.json"] = (output / result["snapshot_id"] / "current.json").read_text(encoding="utf-8")
            for token in ("standing", "_calculated", "Союзники", "Враги", "state_detail", "Физические показатели", "dead_or_captured"):
                with self.subTest(token=token):
                    self.assertEqual([name for name, text in texts.items() if token in text], [])
            published_hashes = tree_hashes(published)
            self.assertEqual(result["snapshot_id"], run_pipeline(**pipeline_args)["snapshot_id"])
            self.assertEqual(tree_hashes(published), published_hashes)
            self.assertEqual(tree_hashes(save), before)

    def test_render_extended_changes_with_unknown_relation(self) -> None:
        changes = {"valid": True, "diplomacy": [{"faction_sid": "other", "relation": {"from": None, "to": 5}}]}
        brief = render_brief({}, changes, "fixture")
        self.assertIn("отношение: None -> 5", brief)
        self.assertNotIn("Изменений в отслеживаемых категориях не зафиксировано", brief)
        self.assertEqual(brief, render_brief({}, changes, "fixture"))

    def test_task_types_dictionary_integrity(self) -> None:
        self.assertEqual(len(TASK_TYPE_NAMES), 289)
        self.assertEqual(get_task_name(58), "JOB_MEDIC")
        self.assertEqual(get_task_name(57), "JOB_REPAIR_ROBOT")
        self.assertEqual(get_task_name(87), "OPERATE_MACHINERY")
        self.assertEqual(get_task_name(124), "OPERATE_STORAGE")
        self.assertEqual(get_task_name(125), "JOB_BUILDER")
        self.assertIsNone(get_task_name(None))
        self.assertEqual(get_task_name(9999), "TASK_9999")

    def test_clean_job_handle(self) -> None:
        # None или пустой
        self.assertIsNone(clean_handle(None))
        self.assertIsNone(clean_job_handle(None))
        # TYPE == 11 (безадресные глобальные задачи типа медика)
        self.assertIsNone(clean_handle((11, 0, 58, 0, 200)))
        self.assertIsNone(clean_job_handle((11, 0, 58, 0, 200)))
        # Все координаты (C, S, I, CS) нулевые
        self.assertIsNone(clean_handle((1, 0, 0, 0, 0)))
        self.assertIsNone(clean_job_handle((1, 0, 0, 0, 0)))
        # Валидный handle станка / сундука / персонажа
        self.assertEqual(clean_handle([1, 5, 500, 1, 600]), [1, 5, 500, 1, 600])
        self.assertEqual(clean_job_handle((1, 5, 500, 1, 600)), [1, 5, 500, 1, 600])

    def test_decode_jobs_synthetic(self) -> None:
        jobs_rec = {
            "type": 67,
            "bool": {"jobs": True},
            "int": {
                "pjob0": 58,
                "pjobT0TYPE": 11, "pjobT0C": 0, "pjobT0S": 100, "pjobT0I": 0, "pjobT0CS": 200,
                "pjob1": 57,
                "pjobT1TYPE": 11, "pjobT1C": 0, "pjobT1S": 101, "pjobT1I": 0, "pjobT1CS": 201,
                "tjob0": 30,
                "tjobT0TYPE": 1, "tjobT0C": 5, "tjobT0S": 500, "tjobT0I": 1, "tjobT0CS": 600,
            },
            "string": {},
        }

        decoded = decode_jobs(jobs_rec)
        self.assertTrue(decoded["enabled"])
        self.assertEqual(len(decoded["primary"]), 2)
        self.assertEqual(decoded["primary"][0]["index"], 0)
        self.assertEqual(decoded["primary"][0]["task_code"], 58)
        self.assertEqual(decoded["primary"][0]["task_name"], "JOB_MEDIC")
        self.assertIsNone(decoded["primary"][0]["target_handle"])

        self.assertEqual(decoded["primary"][1]["index"], 1)
        self.assertEqual(decoded["primary"][1]["task_code"], 57)
        self.assertEqual(decoded["primary"][1]["task_name"], "JOB_REPAIR_ROBOT")
        self.assertIsNone(decoded["primary"][1]["target_handle"])

        self.assertEqual(len(decoded["temporary"]), 1)
        self.assertEqual(decoded["temporary"][0]["task_code"], 30)
        self.assertEqual(decoded["temporary"][0]["target_handle"], [1, 5, 500, 1, 600])

    def test_parse_bounties_multi_faction(self) -> None:
        st_str = {
            "bountyfac0": "defaultEmpireFactionSID",
            "bountyfac1": "11624-Dialogue (10).mod",
        }
        st_int = {
            "bountyexp0": 2000,
            "crimes0": 1,
            "bountyexp1": 1500,
            "crimes1": 0,
        }
        bounties = parse_bounties(st_str, st_int)
        self.assertTrue(bounties["has_bounty"])
        self.assertEqual(bounties["total_amount"], 3500)
        self.assertEqual(len(bounties["entries"]), 2)
        self.assertEqual(bounties["entries"][0]["faction_sid"], "defaultEmpireFactionSID")
        self.assertEqual(bounties["entries"][0]["amount"], 2000)
        self.assertEqual(bounties["entries"][1]["faction_sid"], "11624-Dialogue (10).mod")
        self.assertEqual(bounties["entries"][1]["amount"], 1500)

    def test_parse_character_carrying_and_modes_synthetic(self) -> None:
        # Персонаж 1: несёт на руках
        inst_carrier = {"id": "char_01", "target": "human_template", "states": ["st_36_1"]}
        # Персонаж 2: кого несут
        inst_victim = {"id": "char_02", "target": "human_template", "states": ["st_36_2"]}

        by_sid = {
            "st_36_1": {
                "type": 36,
                "string": {
                    "name": "Несущий Персонаж",
                    "bountyfac0": "1083-gamedata.base",
                },
                "int": {
                    "portrait_serial": 777,
                    "bountyexp0": 15000,
                    "crimes0": 2,
                    "carryingTYPE": 1, "carryingC": 54, "carryingS": 99999, "carryingI": 6, "carryingCS": 88888,
                },
                "bool": {
                    "carrying": True,
                    "taunt mode": True,
                    "ranged mode": True,
                    "passive mode": False,
                    "defensive mode": False,
                },
            },
            "st_36_2": {
                "type": 36,
                "string": {
                    "name": "Переносимый Персонаж",
                },
                "int": {
                    "portrait_serial": 888,
                    "carryingTYPE": 1, "carryingC": 2, "carryingS": 11111, "carryingI": 4, "carryingCS": 22222,
                },
                "bool": {
                    "carrying": False,
                    "taunt mode": False,
                    "ranged mode": False,
                },
            },
        }

        squad_meta = {"squad_id": "squad_test", "squad_name": "Test Squad"}

        carrier = parse_character(inst_carrier, by_sid, squad_meta)
        self.assertIsNotNone(carrier)
        self.assertTrue(carrier["carrying"]["is_carrying"])
        self.assertEqual(carrier["carrying"]["target_handle"], [1, 54, 99999, 6, 88888])
        self.assertIsNone(carrier["carrying"]["carried_by"])
        self.assertTrue(carrier["bounties"]["has_bounty"])
        self.assertEqual(carrier["bounties"]["total_amount"], 15000)
        # Боевые режимы (hold отсутствует в игре, отсутствующие режимы = None)
        modes = carrier["combat_modes"]
        self.assertNotIn("hold", modes)
        self.assertTrue(modes["taunt"])
        self.assertTrue(modes["ranged"])
        self.assertFalse(modes["passive"])
        self.assertFalse(modes["defensive"])
        self.assertIsNone(modes["chase"])
        self.assertIsNone(modes["stealth"])
        self.assertIsNone(modes["non_combat"])

        victim = parse_character(inst_victim, by_sid, squad_meta)
        self.assertIsNotNone(victim)
        self.assertFalse(victim["carrying"]["is_carrying"])
        self.assertIsNone(victim["carrying"]["target_handle"])
        self.assertEqual(victim["carrying"]["carried_by"], [1, 2, 11111, 4, 22222])
        self.assertFalse(victim["bounties"]["has_bounty"])


if __name__ == "__main__":
    unittest.main()
