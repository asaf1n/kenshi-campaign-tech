"""Тесты AC-08, AC-09, AC-10, AC-11: атомарный publisher, fault injection, валидация пакета, детерминизм Markdown."""
from __future__ import annotations

import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.diff import diff
from tools.index import CampaignIndexError
from tools.io_utils import canonical, digest, read_json
from tools.location_registry import REGISTRY_FILENAME
from tools.publisher import PublisherError, publish, recover_if_needed
from tools.rendering import render_brief, render_squad
from tools.timeline import read_timeline
from tools.validation import PublicationValidationError, validate_publication

ROOT = Path(__file__).resolve().parents[1]


def _create_sample_snapshot(
    root: Path,
    snapshot_id: str,
    day: int = 15,
    money: int = 12500,
    parent_snapshot_id: str | None = None,
) -> Path:
    snap_dir = root / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    current_data = {
        "format": "kenshi-session/10",
        "save": {},
        "world": {"day": day, "time": "14:30", "money": money},
        "characters": [
            {
                "id": "ps:1",
                "name": "Хот Лон",
                "squad_id": "sq_main",
                "race": "Скелет",
                "status": {"dead": False, "unconscious": False},
                "attributes_base": {"Strength": 45.0, "Dexterity": 30.0},
                "skills_base": {"Melee Attack": 52.0, "Athletics": 60.0},
                "condition": {"blood": 180.0, "hunger_raw": 3.0},
                "health": [
                    {"part": "Грудь", "flesh": 200.0, "bandage": 0.0, "wear": 0.0, "stun": 0.0},
                    {"part": "Голова", "flesh": 200.0, "bandage": 0.0, "wear": 0.0, "stun": 0.0},
                ],
                "weapons": [{"slot": "weapon_back", "name": "Тесак", "sid": "w_cleaver", "grade": "MK II", "level": 60}],
                "armour": {"chest": {"name": "Панцирь самурая", "sid": "a_samurai", "level": 70}},
                "backpack": {"sid": "bp_1", "name": "Большой рюкзак", "contents": [{"name": "Аптечка", "sid": "med_1", "quantity": 2}]},
                "prosthetics": [],
                "inventory": [{"name": "Вяленое мясо", "sid": "food_meat", "quantity": 5}],
            },
            {
                "id": "ps:2",
                "name": "Кан",
                "squad_id": "sq_main",
                "race": "Шек",
                "status": {"dead": False, "unconscious": False},
                "attributes_base": {"Strength": 50.0},
                "skills_base": {"Heavy Weapons": 40.0},
                "condition": {"blood": 120.0, "hunger_raw": 2.2},
                "health": [],
                "weapons": [],
                "armour": {},
                "backpack": {"contents": []},
                "prosthetics": [],
                "inventory": [],
            },
        ],
        "squads": [{"squad_id": "sq_main", "squad_name": "Штурмовики", "members": ["Хот Лон", "Кан"]}],
        "supplies": {"medkits": {"total": 2, "nutrition": 0}, "food": {"total": 5, "nutrition": 150}},
        "warnings": ["player_towns: город числится за игроком, но постройки в zone/ не обнаружены"],
        "meta": {},
    }

    current_bytes = canonical(current_data)
    prov_data = {
        "format": "kenshi-snapshot/2",
        "snapshot_id": snapshot_id,
        "source_kind": "save",
        "source_format": "kenshi-session/10",
        "source_sha256": SAMPLE_SHA,
        "game_time": {"day": day, "time": "14:30"},
        "current_sha256": digest(current_bytes),
    }

    (snap_dir / "current.json").write_bytes(current_bytes)
    (snap_dir / "provenance.json").write_bytes(canonical(prov_data))
    if parent_snapshot_id is not None:
        # Родитель — атрибут индекса: связь записывается в <snapshots>/../index.json, который читает publisher.
        index_entry(root.parent / "index.json", parent_snapshot_id, None, day=day - 1)
        index_entry(root.parent / "index.json", snapshot_id, parent_snapshot_id, day=day)
    return snap_dir


SAMPLE_SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def index_entry(index_path: Path, snapshot_id: str, parent_snapshot_id: str | None, *, day: int,
                time: str = "14:30", campaign_id: str = "test") -> dict:
    """Запись индекса kenshi-index/2; голова кампании — последний добавленный снимок. Существующая запись не меняется."""
    index = (read_json(index_path.read_bytes()) if index_path.is_file()
             else {"format": "kenshi-index/2", "updated_at": "2026-11-01T00:00:00Z", "campaigns": {}, "snapshots": {}})
    if snapshot_id not in index["snapshots"]:
        index["snapshots"][snapshot_id] = {
            "snapshot_id": snapshot_id, "parent_snapshot_id": parent_snapshot_id, "campaign_id": campaign_id,
            "game_time": {"day": day, "time": time}, "world_ref_id": None, "source_kind": "save",
            "source_sha256": SAMPLE_SHA, "registered_at": f"2026-11-01T00:{len(index['snapshots']):02d}:00Z",
        }
        index["campaigns"][campaign_id] = {"head_snapshot_id": snapshot_id}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(canonical(index))
    return index


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.snapshots_dir = self.root / "snapshots"
        self.diffs_dir = self.root / "diffs"
        self.published_dir = self.root / "published"
        self.published_dir.mkdir(parents=True)
        # Исходный отслеживаемый .gitkeep
        self.gitkeep_file = self.published_dir / ".gitkeep"
        self.gitkeep_file.write_bytes(b"")
        self.index_patcher = patch("tools.publisher.DEFAULT_INDEX_PATH", self.root / "index.json")
        self.index_patcher.start()

    def tearDown(self) -> None:
        self.index_patcher.stop()
        self.temp_dir.cleanup()

    def test_ac08_fault_injection_and_gitkeep_preservation(self) -> None:
        """AC-08: fault injection до записи, при валидации и переключении; сохранение .gitkeep; откат при сбое."""
        snap_id = "snap-init"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        # 1. Первая успешная публикация
        res = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )
        self.assertEqual(res["status"], "published")
        self.assertTrue((self.published_dir / "manifest.json").is_file())
        self.assertTrue((self.published_dir / ".gitkeep").is_file())
        self.assertEqual((self.published_dir / ".gitkeep").read_bytes(), b"")

        manifest_first = (self.published_dir / "manifest.json").read_bytes()

        # 2. Создадим второй снимок
        snap2_id = "snap-second"
        _create_sample_snapshot(self.snapshots_dir, snap2_id, day=16, parent_snapshot_id=snap_id)

        # Fault injection 1: сбой до валидации
        with self.assertRaises(PublisherError):
            publish(
                snapshot_id=snap2_id,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=self.published_dir,
                fault_inject="before_validate",
            )
        # Предыдущая публикация должна остаться нетронутой!
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), manifest_first)
        self.assertEqual((self.published_dir / ".gitkeep").read_bytes(), b"")

        # Fault injection 2: сбой после валидации
        with self.assertRaises(PublisherError):
            publish(
                snapshot_id=snap2_id,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=self.published_dir,
                fault_inject="after_validate",
            )
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), manifest_first)
        self.assertEqual((self.published_dir / ".gitkeep").read_bytes(), b"")

        # Fault injection 3: сбой во время переключения (директория уже в backup)
        with self.assertRaises(PublisherError):
            publish(
                snapshot_id=snap2_id,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=self.published_dir,
                fault_inject="during_switch",
            )
        # Откат обязан восстановить предыдущий пакет и .gitkeep
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), manifest_first)
        self.assertEqual((self.published_dir / ".gitkeep").read_bytes(), b"")

        # 3. Самовосстановление при следующем запуске, если backup остался после краша
        # Сымитируем падение ОС/процесса: published переименован в backup, а staging остался
        backup_dir = self.published_dir.parent / f"{self.published_dir.name}.backup"
        staging_dir = self.published_dir.parent / f"{self.published_dir.name}.staging"
        self.published_dir.rename(backup_dir)
        staging_dir.mkdir()
        (staging_dir / "partial.json").write_text("crash", encoding="utf-8")

        # recover_if_needed восстанавливает published_dir
        recover_if_needed(self.published_dir, backup_dir, staging_dir)
        self.assertTrue(self.published_dir.is_dir())
        self.assertFalse(backup_dir.exists())
        self.assertFalse(staging_dir.exists())
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), manifest_first)
        self.assertEqual((self.published_dir / ".gitkeep").read_bytes(), b"")

    def test_headers_without_hashes_and_empty_timeline_warning(self) -> None:
        """Заголовки brief.md и squad.md по игровому времени без sha256; пустой timeline.jsonl даёт warning при status passed."""
        snap_id = "snap-headers"
        _create_sample_snapshot(self.snapshots_dir, snap_id)
        kwargs = dict(snapshot_id=snap_id, snapshots_dir=self.snapshots_dir, diffs_dir=self.diffs_dir,
                      published_dir=self.published_dir)
        publish(**kwargs)

        cur = read_json((self.published_dir / "extended" / "current.json").read_bytes())
        when = f"День {cur['world']['day']}, {cur['world']['time']}"
        self.assertEqual((self.published_dir / "brief.md").read_text(encoding="utf-8").splitlines()[0], f"# Краткая сводка: {when}")
        self.assertEqual((self.published_dir / "squad.md").read_text(encoding="utf-8").splitlines()[0], f"# Состав отряда: {when}")
        manifest_bytes = (self.published_dir / "manifest.json").read_bytes()
        manifest = read_json(manifest_bytes)
        validation = manifest["validation"]
        self.assertEqual((validation["status"], validation["warnings"]), ("passed", ["artifact:timeline: empty"]))
        self.assertEqual(manifest["artifacts"]["location_registry"], REGISTRY_FILENAME)
        registry = (self.published_dir / REGISTRY_FILENAME).read_bytes()

        publish(**kwargs)  # предупреждение не нарушает идемпотентность выпуска
        self.assertEqual((self.published_dir / "manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual((self.published_dir / REGISTRY_FILENAME).read_bytes(), registry)

    def test_publish_does_not_hide_an_unreadable_index(self) -> None:
        snap_id = "snap-broken-index"
        _create_sample_snapshot(self.snapshots_dir, snap_id)
        (self.root / "index.json").write_text("[", encoding="utf-8")
        with self.assertRaises(CampaignIndexError):
            publish(snapshot_id=snap_id, snapshots_dir=self.snapshots_dir, diffs_dir=self.diffs_dir,
                    published_dir=self.published_dir)
        self.assertFalse((self.published_dir / "manifest.json").exists())

    def test_publish_has_no_current_dir_and_legacy_manifest_stays_valid(self) -> None:
        """Публикация не создаёт current/ и удаляет устаревший; манифест без current_index валиден, прежний с ним тоже."""
        snap_id = "snap-nocurrent"
        _create_sample_snapshot(self.snapshots_dir, snap_id)
        (self.published_dir / "current").mkdir()
        (self.published_dir / "current/index.json").write_text("{}", encoding="utf-8")
        publish(snapshot_id=snap_id, snapshots_dir=self.snapshots_dir, diffs_dir=self.diffs_dir,
                published_dir=self.published_dir)

        self.assertFalse((self.published_dir / "current").exists())
        manifest_path = self.published_dir / "manifest.json"
        manifest = read_json(manifest_path.read_bytes())
        self.assertNotIn("current_index", manifest["artifacts"])
        self.assertEqual(validate_publication(self.published_dir)["status"], "passed")

        (self.published_dir / "current").mkdir()
        (self.published_dir / "current/index.json").write_text("{}", encoding="utf-8")
        legacy = copy.deepcopy(manifest)
        legacy["artifacts"]["current_index"] = "current/index.json"
        manifest_path.write_bytes(canonical(legacy))
        self.assertEqual(validate_publication(self.published_dir)["status"], "passed")

    def test_ac09_package_validation_and_rejected_escapes(self) -> None:
        """AC-09: валидация пакета, отвержение невалидных путей/ссылок/выходов за пределы."""
        snap_id = "snap-val"
        _create_sample_snapshot(self.snapshots_dir, snap_id)
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )

        # Валидный опубликованный пакет
        res = validate_publication(self.published_dir)
        self.assertEqual(res["status"], "passed")

        manifest_path = self.published_dir / "manifest.json"
        manifest_orig = read_json(manifest_path.read_bytes())

        # Негативный сценарий 1: выход через ../ в artifacts
        bad_manifest = copy.deepcopy(manifest_orig)
        bad_manifest["artifacts"]["brief"] = "../escape.md"
        manifest_path.write_bytes(canonical(bad_manifest))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        # Негативный сценарий 2: абсолютный путь в artifacts
        bad_manifest = copy.deepcopy(manifest_orig)
        bad_manifest["artifacts"]["brief"] = "C:/Windows/notepad.exe"
        manifest_path.write_bytes(canonical(bad_manifest))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        # Негативный сценарий 3: отсутствующий артефакт
        bad_manifest = copy.deepcopy(manifest_orig)
        bad_manifest["artifacts"]["brief"] = "missing.md"
        manifest_path.write_bytes(canonical(bad_manifest))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        # Негативный сценарий 4: несовпадение snapshot_id между manifest и changes.to
        bad_manifest = copy.deepcopy(manifest_orig)
        bad_manifest["snapshot_id"] = "wrong-id"
        manifest_path.write_bytes(canonical(bad_manifest))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        # Негативный сценарий 5: parent_snapshot_id указан, а в changes from: null
        bad_manifest = copy.deepcopy(manifest_orig)
        bad_manifest["parent_snapshot_id"] = "fake-parent"
        manifest_path.write_bytes(canonical(bad_manifest))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        manifest_path.write_bytes(canonical(manifest_orig))
        registry_path = self.published_dir / REGISTRY_FILENAME
        registry = read_json(registry_path.read_bytes())
        registry["snapshot_id"] = "another-snapshot"
        registry_path.write_bytes(canonical(registry))
        with self.assertRaisesRegex(PublicationValidationError, "реестр|Реестр"):
            validate_publication(self.published_dir)

    def test_ac10_ac11_privacy_and_deterministic_rendering_reconciliation(self) -> None:
        """AC-10, AC-11: отсутствие персональных путей, детерминизм рендера, сверка чисел."""
        snap_id = "snap-det"
        _create_sample_snapshot(self.snapshots_dir, snap_id, day=25, money=77700)
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )

        cur = read_json((self.published_dir / "extended" / "current.json").read_bytes())
        ch = read_json((self.published_dir / "changes.json").read_bytes())
        loc_path = self.published_dir / "locations.json"
        loc = read_json(loc_path.read_bytes()) if loc_path.is_file() else None

        # 1. Повторный рендер даёт побайтово идентичные файлы (детерминизм)
        timeline = read_timeline(self.published_dir / "timeline.jsonl", projection=True)
        brief1 = render_brief(cur, ch, snap_id, locations=loc, timeline=timeline, timeline_projection=True)
        brief2 = render_brief(cur, ch, snap_id, locations=loc, timeline=timeline, timeline_projection=True)
        self.assertEqual(brief1, brief2)

        squad1 = render_squad(cur, snap_id, locations=loc)
        squad2 = render_squad(cur, snap_id, locations=loc)
        self.assertEqual(squad1, squad2)

        manifest = read_json((self.published_dir / "manifest.json").read_bytes())
        published_brief = render_brief(cur, ch, snap_id, locations=loc, timeline=timeline, timeline_projection=True,
                                       publication=manifest)
        self.assertEqual((self.published_dir / "brief.md").read_text(encoding="utf-8"), published_brief)
        self.assertEqual((self.published_dir / "squad.md").read_text(encoding="utf-8"), squad1)

        # 2. Сверка чисел в brief.md и squad.md с JSON
        # День игры
        self.assertIn("День 25", brief1)
        self.assertIn("время 14:30", brief1)
        # Баланс
        self.assertIn("77 700 кат.", brief1)
        # Число участников и отрядов
        self.assertIn("**Всего персонажей:** 2", brief1)
        self.assertIn("**Всего отрядов:** 1", brief1)
        # Наличие участников по ID
        self.assertIn("ps:1", brief1)
        self.assertIn("ps:2", brief1)
        self.assertIn("Хот Лон", brief1)
        self.assertIn("Кан", brief1)
        # Ссылки на артефакты
        self.assertNotIn("current/index.json", brief1)
        self.assertIn("(extended/current.json)", brief1)
        self.assertIn("[Детальные изменения (changes.json)](changes.json)", brief1)
        self.assertIn("[Полный состав отряда (squad.md)](squad.md)", brief1)
        # Сведения о местоположении: «не получено» в fallback-режиме без locations
        brief_fallback = render_brief(cur, ch, snap_id)
        self.assertIn("не получено (географические координаты не разрешены)", brief_fallback)
        if loc is not None:
            self.assertIn("[Пространственное разрешение локаций (locations.json)](locations.json)", brief1)

        # 3. Проверка squad.md
        self.assertIn("### Хот Лон (`ps:1`)", squad1)
        self.assertIn("**Раса:** Скелет", squad1)
        self.assertIn("strength: 45.0", squad1.lower())
        self.assertIn("тесак", squad1.lower())

        # 4. Проверка отсутствия локальных путей пользователя и секретов
        for art_path in self.published_dir.rglob("*"):
            if art_path.name == ".gitkeep" or not art_path.is_file():
                continue
            text = art_path.read_text(encoding="utf-8", errors="replace")
            for forbidden in ("Users/artem", "Users\\\\artem", "/home/", "/root/", "Bearer ", "sk-"):
                self.assertNotIn(forbidden, text)

        # 5. Негативный сценарий приватности: если в артефакт подмешан локальный путь
        (self.published_dir / "brief.md").write_text("Secret leak: C:/Users/artem/private.txt\n", encoding="utf-8")
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

    def test_canonical_published_guard_and_campaign_validation(self) -> None:
        """Проверка привязки к campaign_id: защита канонического published/ и сверка с index.json."""
        snap_id = "sha256-sample-campaign-test"
        _create_sample_snapshot(self.snapshots_dir, snap_id, day=10)

        # 1. Попытка публикации не-hot-lon в канонический published/ вызывает отказ
        with self.assertRaises(PublisherError) as ctx:
            publish(
                snapshot_id=snap_id,
                snapshots_dir=self.snapshots_dir,
                diffs_dir=self.diffs_dir,
                published_dir=ROOT / "published",
                campaign_id="foreign-campaign",
            )
        self.assertIn("hot-lon", str(ctx.exception))

        # 2. Успешная публикация в тестовый каталог с campaign_id
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
            campaign_id="test-campaign",
        )
        manifest = read_json((self.published_dir / "manifest.json").read_bytes())
        self.assertEqual(manifest.get("campaign_id"), "test-campaign")

        # 3. Валидация отвергает пакет при расхождении с index.json
        mismatched_index = {
            "format": "kenshi-index/2",
            "updated_at": "2026-11-01T00:00:00Z",
            "campaigns": {"expected-index-campaign": {"head_snapshot_id": snap_id}},
            "snapshots": {
                snap_id: {
                    "snapshot_id": snap_id,
                    "parent_snapshot_id": None,
                    "campaign_id": "expected-index-campaign",
                    "game_time": {"day": 10, "time": "14:30"},
                    "world_ref_id": None,
                    "source_kind": "save",
                    "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "registered_at": "2026-11-01T00:00:00Z",
                }
            },
        }
        with self.assertRaises(PublicationValidationError) as ctx_val:
            validate_publication(self.published_dir, index_data=mismatched_index)
        self.assertIn("campaign_id", str(ctx_val.exception))
