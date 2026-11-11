"""Тесты интеграции unique_entities_registry в publisher и validate_publication."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.io_utils import canonical, read_json
from tools.publisher import UNIQUE_ENTITIES_FILENAME, publish
from tools.validation import PublicationValidationError, validate_publication
from tests.test_publish import _create_sample_snapshot


class PublisherUniqueEntitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.snapshots_dir = self.root / "snapshots"
        self.diffs_dir = self.root / "diffs"
        self.published_dir = self.root / "published"
        self.published_dir.mkdir(parents=True)
        (self.published_dir / ".gitkeep").write_bytes(b"")
        self.index_patcher = patch("tools.publisher.DEFAULT_INDEX_PATH", self.root / "index.json")
        self.index_patcher.start()

    def tearDown(self) -> None:
        self.index_patcher.stop()
        self.temp_dir.cleanup()

    def test_publish_includes_unique_entities_registry_and_validates(self) -> None:
        """Публикация включает unique_entities_registry.json, манифест валиден, validate_publication проходит."""
        snap_id = "snap-unique-test"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        res = publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )
        self.assertEqual(res["status"], "published")

        manifest_path = self.published_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = read_json(manifest_path.read_bytes())

        self.assertIn("unique_entities_registry", manifest["artifacts"])
        self.assertEqual(manifest["artifacts"]["unique_entities_registry"], UNIQUE_ENTITIES_FILENAME)
        self.assertIn("unique_entities_registry", manifest["validation"]["checks"])

        registry_path = self.published_dir / UNIQUE_ENTITIES_FILENAME
        self.assertTrue(registry_path.is_file())
        registry = read_json(registry_path.read_bytes())

        self.assertEqual(registry["format"], "kenshi-unique-entities-registry/1")
        self.assertEqual(registry["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(registry["campaign_id"], manifest["campaign_id"])
        self.assertEqual(registry["world_ref_id"], manifest["world_ref_id"])
        self.assertEqual(registry["game_time"], manifest["game_time"])
        self.assertEqual(registry["generated_at"], manifest["generated_at"])
        self.assertIn("unique_npcs", registry)
        self.assertIn("bosses", registry)
        self.assertIn("meitou_weapons", registry)

        # Валидация публикации
        validation_result = validate_publication(self.published_dir)
        self.assertEqual(validation_result["status"], "passed")
        self.assertIn("unique_entities_registry", validation_result["checks"])

    def test_publish_idempotency_with_unique_entities_registry(self) -> None:
        """Повторный запуск publish для того же неизменённого снимка сохраняет побайтовую идентичность."""
        snap_id = "snap-idempotent"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )

        manifest_first = (self.published_dir / "manifest.json").read_bytes()
        registry_first = (self.published_dir / UNIQUE_ENTITIES_FILENAME).read_bytes()

        # Повторная публикация
        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )

        manifest_second = (self.published_dir / "manifest.json").read_bytes()
        registry_second = (self.published_dir / UNIQUE_ENTITIES_FILENAME).read_bytes()

        self.assertEqual(manifest_first, manifest_second)
        self.assertEqual(registry_first, registry_second)

    def test_validate_publication_fails_on_corrupted_or_mismatched_registry(self) -> None:
        """validate_publication отвергает повреждённый или несогласованный unique_entities_registry.json."""
        snap_id = "snap-validate-err"
        _create_sample_snapshot(self.snapshots_dir, snap_id)

        publish(
            snapshot_id=snap_id,
            snapshots_dir=self.snapshots_dir,
            diffs_dir=self.diffs_dir,
            published_dir=self.published_dir,
        )

        registry_path = self.published_dir / UNIQUE_ENTITIES_FILENAME
        registry = read_json(registry_path.read_bytes())

        # Несовпадение snapshot_id
        tampered = dict(registry, snapshot_id="wrong_snapshot")
        registry_path.write_bytes(canonical(tampered))
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)

        # Невалидный JSON / схема
        registry_path.write_bytes(b"invalid-json")
        with self.assertRaises(PublicationValidationError):
            validate_publication(self.published_dir)


if __name__ == "__main__":
    unittest.main()
