"""Тесты AC-05, AC-06, AC-07: реестр снимков (kenshi-index/1), родительство, reload и выбор ветки."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import unittest

from jsonschema import ValidationError
from tools.diff import diff, diff_snapshots
from tools.index import (
    CampaignIndexError,
    check_ancestry,
    check_branch_compatibility,
    empty_index,
    get_branch_tree,
    load_index,
    rebuild_index,
    register_snapshot,
    save_index,
    switch_branch,
)
from tools.io_utils import canonical, read_json
from tools.locator import resolve_locations
from tools.publisher import PublisherError, publish
from tools.validation import ROOT, validate, validate_index
from tools.world_reference import DEFAULT_WORLD_DIR, load_world_reference

try:
    SAMPLE_WREF = load_world_reference("sha256-798a47da89d9ac378677dc5a6e0b23714a68d83ea26e774c8825bdfa8b0f20ec")
except Exception:
    SAMPLE_WREF = read_json((ROOT / "examples/world-reference.example.json").read_bytes())


def _make_snapshot_data(
    day: int = 10,
    time_str: str = "12:00",
    money: int = 5000,
    chars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    characters = chars or [
        {
            "id": "ps:1",
            "name": "Хорек",
            "squad_id": "sq_1",
            "squad_name": "Беглецы",
            "race": "Greenlander",
            "status": {"dead": False, "unconscious": False},
            "attributes_base": {"Strength": 20.0},
            "skills_base": {"Athletics": 30.0},
            "inventory": [{"name": "Аптечка", "sid": "1359-gamedata.base", "quantity": 3}],
            "coordinates": [-1000.0, 50.0, -2000.0],
        }
    ]
    return {
        "format": "kenshi-session/10",
        "save": {"name": "save_test", "kind": "manual", "saved_at": "2026-11-01T12:00:00Z"},
        "world": {"day": day, "time": time_str, "money": money},
        "characters": characters,
        "squads": [{"squad_id": "sq_1", "squad_name": "Беглецы", "members": ["Хорек"]}],
        "supplies": {"medkits": {"total": 3, "nutrition": 0}},
        "property": {"buildings": []},
        "warnings": [],
        "meta": {"session_file": "session.json"},
    }


def _create_snapshot_dir(
    snapshots_dir: Path,
    snapshot_id: str,
    day: int,
    time_str: str,
    parent_snapshot_id: str | None = None,
    branch_id: str | None = None,
    campaign_id: str = "default",
    coords: tuple[float, float, float] = (-1000.0, 50.0, -2000.0),
) -> Path:
    sdir = snapshots_dir / snapshot_id
    sdir.mkdir(parents=True, exist_ok=True)

    chars = [
        {
            "id": "ps:1",
            "name": "Хорек",
            "squad_id": "sq_1",
            "squad_name": "Беглецы",
            "race": "Greenlander",
            "status": {"dead": False, "unconscious": False},
            "attributes_base": {"Strength": 20.0 + day},
            "skills_base": {"Athletics": 30.0 + day},
            "inventory": [{"name": "Аптечка", "sid": "1359-gamedata.base", "quantity": 3}],
            "coordinates": [coords[0], coords[1], coords[2]],
        }
    ]
    cur_data = _make_snapshot_data(day=day, time_str=time_str, money=5000 + day * 100, chars=chars)
    cur_bytes = canonical(cur_data)
    (sdir / "current.json").write_bytes(cur_bytes)

    source_sha = hashlib.sha256(f"source-{snapshot_id}".encode()).hexdigest()
    prov_data = {
        "format": "kenshi-snapshot/1",
        "snapshot_id": snapshot_id,
        "parent_snapshot_id": parent_snapshot_id,
        "branch_id": branch_id,
        "campaign_id": campaign_id,
        "source_kind": "save",
        "source_format": "kenshi-session/10",
        "source_sha256": source_sha,
        "world_ref_id": SAMPLE_WREF["world_ref_id"],
        "game_time": {"day": day, "time": time_str},
        "registered_at": "2026-11-01T12:00:00Z",
    }
    (sdir / "provenance.json").write_bytes(canonical(prov_data))
    return sdir


def _hash_directory(target_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not target_dir.is_dir():
        return hashes
    for p in sorted(target_dir.iterdir()):
        if p.is_file():
            hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


class BranchAndIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="kenshi-test-branches-")
        self.root = Path(self.temp_dir)
        self.snapshots_dir = self.root / "snapshots"
        self.diffs_dir = self.root / "diffs"
        self.published_dir = self.root / "published"
        self.index_file = self.root / "index.json"

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.diffs_dir.mkdir(parents=True, exist_ok=True)
        self.published_dir.mkdir(parents=True, exist_ok=True)
        (self.published_dir / ".gitkeep").write_bytes(b"")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_schema_and_example(self) -> None:
        """Валидация схемы contracts/index.schema.json и примера examples/index.example.json."""
        example_path = ROOT / "examples/index.example.json"
        self.assertTrue(example_path.is_file())
        data = read_json(example_path.read_bytes())
        validate(data, "index")
        summary = validate_index(data)
        self.assertEqual(summary["status"], "passed")
        self.assertIn("main", data["branches"])
        self.assertIn("reload-1", data["branches"])

    def test_negative_failures_cycles_orphans_duplicates(self) -> None:
        """Проверка отказов: циклы, сироты, дубликаты ID с конфликтами."""
        idx = empty_index()
        save_index(idx, self.index_file)

        # 1. Снимок не может быть собственным родителем
        with self.assertRaises(CampaignIndexError):
            register_snapshot("snap_a", parent_snapshot_id="snap_a", index_path=self.index_file)

        # 2. Нельзя сослаться на отсутствующего родителя (сирота)
        with self.assertRaises(CampaignIndexError):
            register_snapshot("snap_b", parent_snapshot_id="non_existent", index_path=self.index_file)

        # 3. Успешная регистрация корня snap_a и потомка snap_b
        register_snapshot("snap_a", parent_snapshot_id=None, index_path=self.index_file, source_sha256="a" * 64)
        register_snapshot("snap_b", parent_snapshot_id="snap_a", index_path=self.index_file, source_sha256="b" * 64)

        # 4. Идемпотентный повтор регистрации snap_a разрешен
        same = register_snapshot("snap_a", parent_snapshot_id=None, index_path=self.index_file, source_sha256="a" * 64)
        self.assertEqual(same["snapshot_id"], "snap_a")

        # 5. Конфликтующая повторная регистрация вызывает отказ
        with self.assertRaises(CampaignIndexError):
            register_snapshot("snap_a", parent_snapshot_id=None, index_path=self.index_file, source_sha256="c" * 64)

        # 6. Цикл в графе (A -> B -> C -> A)
        register_snapshot("snap_c", parent_snapshot_id="snap_b", index_path=self.index_file, source_sha256="c" * 64)

        # Подмешивание цикла в структуру проверяется валидатором
        bad_idx = load_index(self.index_file)
        bad_idx["snapshots"]["snap_a"]["parent_snapshot_id"] = "snap_c"
        with self.assertRaises(ValueError):
            validate_index(bad_idx)

        # 7. Схема отвергает некорректные свойства и форматы
        base_example = read_json((ROOT / "examples/index.example.json").read_bytes())
        with self.assertRaises(ValidationError):
            validate(base_example | {"unexpected": 123}, "index")
        with self.assertRaises(ValidationError):
            validate(base_example | {"updated_at": "not-a-date"}, "index")
        with self.assertRaises(ValidationError):
            bad_snap = copy.deepcopy(base_example)
            bad_sid = list(bad_snap["snapshots"].keys())[0]
            bad_snap["snapshots"][bad_sid]["source_sha256"] = "invalid-hash"
            validate(bad_snap, "index")

    def test_ancestry_and_game_time_regression(self) -> None:
        """AC-05: проверка предков, несовместимости веток и регрессии времени (to < from)."""
        idx = empty_index()
        save_index(idx, self.index_file)

        register_snapshot("snap_a", parent_snapshot_id=None, branch_id="main", index_path=self.index_file, source_sha256="a"*64)
        register_snapshot("snap_b", parent_snapshot_id="snap_a", branch_id="main", index_path=self.index_file, source_sha256="b"*64)
        register_snapshot("snap_c", parent_snapshot_id="snap_a", branch_id="alt", index_path=self.index_file, source_sha256="c"*64)

        current_idx = load_index(self.index_file)

        # A предок B и C
        self.assertTrue(check_ancestry(current_idx, "snap_a", "snap_b"))
        self.assertTrue(check_ancestry(current_idx, "snap_a", "snap_c"))

        # B не предок C, C не предок B
        self.assertFalse(check_ancestry(current_idx, "snap_b", "snap_c"))
        self.assertFalse(check_ancestry(current_idx, "snap_c", "snap_b"))

        # Совместимость веток:
        compat_ab, _ = check_branch_compatibility(current_idx, "snap_a", "snap_b")
        self.assertTrue(compat_ab)

        compat_bc, reason_bc = check_branch_compatibility(current_idx, "snap_b", "snap_c")
        self.assertFalse(compat_bc)
        self.assertEqual(reason_bc, "different_branches")

        # Регрессия игрового времени (to < from)
        prev_snap = _make_snapshot_data(day=12, time_str="14:00")
        cur_snap_reg = _make_snapshot_data(day=10, time_str="10:00")

        res_reg = diff(prev=prev_snap, cur=cur_snap_reg, prev_id="prev", cur_id="cur")
        self.assertFalse(res_reg["valid"])
        self.assertEqual(res_reg["reason"], "game_time_regression")

    def test_ac06_reload_branching_and_isolation(self) -> None:
        """AC-06: сценарий reload A -> B и A -> C:

        - game_time(C) > game_time(B) при общем родителе A не объединяет ветки!
        - Попытка обычного diff B -> C дает valid: false, reason: 'different_branches',
          без ложных потерь ресурсов, навыков и без ложных перемещений в locations.movement.
        - Попытка публикации B -> C отклоняется, хеши published/ не меняются.
        - Явный выбор ветки C дает корректную публикацию (с родителем A либо без baseline).
        """
        # Снимок A: День 10, 10:00 (корень)
        snap_a = "sha256-" + "a" * 64
        _create_snapshot_dir(self.snapshots_dir, snap_a, day=10, time_str="10:00", parent_snapshot_id=None, branch_id="main", coords=(-1000.0, 50.0, -2000.0))

        # Снимок B: День 10, 14:00 (ветка main, родитель A)
        snap_b = "sha256-" + "b" * 64
        _create_snapshot_dir(self.snapshots_dir, snap_b, day=10, time_str="14:00", parent_snapshot_id=snap_a, branch_id="main", coords=(-500.0, 50.0, -1000.0))

        # Снимок C (reload из A): День 10, 16:00 (ветка reload-1, родитель A)
        # Обратите внимание: game_time(C) 16:00 > game_time(B) 14:00!
        snap_c = "sha256-" + "c" * 64
        _create_snapshot_dir(self.snapshots_dir, snap_c, day=10, time_str="16:00", parent_snapshot_id=snap_a, branch_id="reload-1", coords=(2000.0, 50.0, 3000.0))

        # 1. Регистрация снимков в data/index.json
        register_snapshot(snap_a, parent_snapshot_id=None, branch_id="main", game_time={"day": 10, "time": "10:00"}, world_ref_id=SAMPLE_WREF["world_ref_id"], index_path=self.index_file, source_sha256="a"*64)
        register_snapshot(snap_b, parent_snapshot_id=snap_a, branch_id="main", game_time={"day": 10, "time": "14:00"}, world_ref_id=SAMPLE_WREF["world_ref_id"], index_path=self.index_file, source_sha256="b"*64)
        register_snapshot(snap_c, parent_snapshot_id=snap_a, branch_id="reload-1", game_time={"day": 10, "time": "16:00"}, world_ref_id=SAMPLE_WREF["world_ref_id"], index_path=self.index_file, source_sha256="c"*64)

        # 2. Публикуем A, затем B
        publish(
            snapshot_id=snap_a,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            parent_snapshot_id=None,
            branch_id="main",
            world_ref_id=SAMPLE_WREF["world_ref_id"],
            index_path=self.index_file,
        )
        publish(
            snapshot_id=snap_b,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            parent_snapshot_id=snap_a,
            branch_id="main",
            world_ref_id=SAMPLE_WREF["world_ref_id"],
            index_path=self.index_file,
        )

        # Фиксируем хеши файлов published/ после публикации B
        hashes_after_b = _hash_directory(self.published_dir)
        self.assertIn("manifest.json", hashes_after_b)
        self.assertIn("current.json", hashes_after_b)
        self.assertIn("changes.json", hashes_after_b)
        self.assertIn("locations.json", hashes_after_b)

        # 3. Попытка сравнения B -> C:
        # B и C — параллельные ветки, B не является родителем C (родитель C — A).
        diff_bc = diff_snapshots(
            prev_dir=self.snapshots_dir / snap_b,
            cur_dir=self.snapshots_dir / snap_c,
            output_dir=self.diffs_dir,
            parent_snapshot_id=snap_b,
            index_path=self.index_file,
        )
        self.assertFalse(diff_bc["valid"])
        self.assertEqual(diff_bc["reason"], "different_branches")
        # Нет ложных потерь навыков, предметов, участников
        self.assertEqual(diff_bc["skills"], [])
        self.assertEqual(diff_bc["equipment"], [])
        self.assertEqual(diff_bc["inventory"], [])
        self.assertEqual(diff_bc["transfers"], [])
        self.assertEqual(diff_bc["roster"], {})

        # Пространственное перемещение (movement) синхронно получает valid: false
        prev_loc = read_json((self.published_dir / "locations.json").read_bytes())
        cur_c = read_json((self.snapshots_dir / snap_c / "current.json").read_bytes())
        loc_bc = resolve_locations(
            current=cur_c,
            world_ref=SAMPLE_WREF,
            snapshot_id=snap_c,
            previous_locations=prev_loc,
            changes=diff_bc,
        )
        self.assertFalse(loc_bc["movement"]["valid"])
        self.assertEqual(loc_bc["movement"]["reason"], "different_branches")
        self.assertEqual(loc_bc["movement"]["characters"], [])

        # 4. Попытка неразрешенной публикации B -> C обязана завершиться ошибкой
        with self.assertRaises(PublisherError):
            publish(
                snapshot_id=snap_c,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=self.published_dir,
                parent_snapshot_id=snap_b,
                world_ref_id=SAMPLE_WREF["world_ref_id"],
                index_path=self.index_file,
            )

        # Хеши published/ строго неизменны после отклоненной публикации
        hashes_after_failed_c = _hash_directory(self.published_dir)
        self.assertEqual(hashes_after_b, hashes_after_failed_c)

        # 5. Явный выбор ветки reload-1 разрешает публикацию C
        # Сравнение ведется со своим легитимным предком A (а не B!)
        pub_c_res = publish(
            snapshot_id=snap_c,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            parent_snapshot_id=snap_a,
            branch_id="reload-1",
            world_ref_id=SAMPLE_WREF["world_ref_id"],
            index_path=self.index_file,
        )
        self.assertEqual(pub_c_res["status"], "published")

        # Проверяем результат успешной публикации C
        c_manifest = read_json((self.published_dir / "manifest.json").read_bytes())
        self.assertEqual(c_manifest["snapshot_id"], snap_c)
        self.assertEqual(c_manifest["parent_snapshot_id"], snap_a)
        self.assertEqual(c_manifest["branch_id"], "reload-1")

        c_changes = read_json((self.published_dir / "changes.json").read_bytes())
        self.assertTrue(c_changes["valid"])
        self.assertEqual(c_changes["from"]["snapshot_id"], snap_a)
        self.assertEqual(c_changes["to"]["snapshot_id"], snap_c)

        c_locs = read_json((self.published_dir / "locations.json").read_bytes())
        self.assertTrue(c_locs["movement"]["valid"])
        self.assertEqual(c_locs["movement"]["from_snapshot_id"], snap_a)
        self.assertEqual(c_locs["movement"]["to_snapshot_id"], snap_c)

    def test_rebuild_index_and_branch_operations(self) -> None:
        """AC-07: атомарность записи индекса, восстановление rebuild_index и операции ветвления."""
        # Создаем три снимка на диске
        snap_1 = "sha256-" + "1" * 64
        snap_2 = "sha256-" + "2" * 64
        snap_3 = "sha256-" + "3" * 64

        _create_snapshot_dir(self.snapshots_dir, snap_1, day=5, time_str="08:00", parent_snapshot_id=None, branch_id="main")
        _create_snapshot_dir(self.snapshots_dir, snap_2, day=6, time_str="12:00", parent_snapshot_id=snap_1, branch_id="main")
        _create_snapshot_dir(self.snapshots_dir, snap_3, day=6, time_str="16:00", parent_snapshot_id=snap_1, branch_id="alt")

        # Восстановление реестра из каталогов snapshots
        rebuilt = rebuild_index(self.snapshots_dir, index_path=self.index_file)
        self.assertEqual(rebuilt["format"], "kenshi-index/1")
        self.assertEqual(len(rebuilt["snapshots"]), 3)
        self.assertIn("main", rebuilt["branches"])
        self.assertIn("alt", rebuilt["branches"])
        self.assertEqual(rebuilt["branches"]["main"]["head_snapshot_id"], snap_2)
        self.assertEqual(rebuilt["branches"]["alt"]["head_snapshot_id"], snap_3)

        # Валидация восстановленного индекса
        summary = validate_index(rebuilt)
        self.assertEqual(summary["status"], "passed")

        # Переключение ветки
        switched = switch_branch("alt", index_path=self.index_file)
        self.assertEqual(switched["active_branch_id"], "alt")

        # Создание новой ветки через switch
        new_b = switch_branch("experimental", create=True, name="Эксперимент", index_path=self.index_file)
        self.assertEqual(new_b["active_branch_id"], "experimental")
        self.assertIn("experimental", new_b["branches"])

        # Дерево снимков и веток
        tree = get_branch_tree(new_b)
        self.assertEqual(tree["active_branch_id"], "experimental")
        self.assertEqual(len(tree["roots"]), 1)
        root_node = tree["roots"][0]
        self.assertEqual(root_node["snapshot_id"], snap_1)
        self.assertEqual(len(root_node["children"]), 2)

    def test_campaign_cli_index_and_branch(self) -> None:
        """Проверка CLI-команд tools.campaign: index show/rebuild/validate, branch list/switch/tree."""
        from tools.campaign import main as campaign_main

        snap_1 = "sha256-" + "1" * 64
        _create_snapshot_dir(self.snapshots_dir, snap_1, day=5, time_str="08:00", parent_snapshot_id=None, branch_id="main")

        # rebuild через CLI
        code = campaign_main(["index", "rebuild", "--snapshots-dir", str(self.snapshots_dir), "--output", str(self.index_file)])
        self.assertEqual(code, 0)
        self.assertTrue(self.index_file.is_file())

        # validate через CLI
        code = campaign_main(["index", "validate", "--index-file", str(self.index_file)])
        self.assertEqual(code, 0)

        # show через CLI
        code = campaign_main(["index", "show", "--index-file", str(self.index_file)])
        self.assertEqual(code, 0)

        # branch list через CLI
        code = campaign_main(["branch", "list", "--index-file", str(self.index_file)])
        self.assertEqual(code, 0)

        # branch switch с флагом --create через CLI
        code = campaign_main(["branch", "switch", "feature-x", "--create", "--name", "Feature X", "--index-file", str(self.index_file)])
        self.assertEqual(code, 0)

        # branch tree через CLI
        code = campaign_main(["branch", "tree", "--index-file", str(self.index_file)])
        self.assertEqual(code, 0)
