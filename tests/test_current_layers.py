"""Публичная проекция снимка, схема манифеста без current/ и доставка, удаляющая устаревший current/."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from tools.current_layers import public_projection
from tools.deliver_companion import deliver_to_companion
from tools.io_utils import canonical
from tools.validation import START_SET_LIMIT_CHARS, PublicationValidationError, validate_start_set_size

ROOT = Path(__file__).resolve().parents[1]

SNAPSHOT = {
    "format": "kenshi-session/11",
    "characters": [
        {
            "id": "ps:-5", "name": "Боец", "squad_name": "Отряд",
            "skills_base": {"Dodge": 1.0, "Katanas": 20.5, "Toughness": 0.4, "Athletics": 1.0000001},
            "hidden_stats_raw": {"x": 1},
            "items": [{"item_sid": "a", "is_stolen": True, "raw": {"int": {"ownedbyTYPE": 1}}}],
            "carrying": {"is_carrying": False, "raw": {"carryingTYPE": 11}},
        },
    ],
    "property": {"buildings": [{"id": "1-zone", "name": "Дом", "interior": [{"raw": 1, "x": 2}]}]},
}


def _make_bundle(src: Path) -> None:
    (src / "extended").mkdir(parents=True)
    (src / "extended/current.json").write_bytes(b"{}")
    (src / "manifest.json").write_bytes(canonical({"validation": {"status": "passed"}}))


class CurrentLayersTests(unittest.TestCase):
    def test_projection_drops_raw_hidden_and_default_skills_only(self) -> None:
        """Проекция убирает raw, hidden_stats_raw и навыки строго 1.0; штрафы < 1.0, > 1.0 и is_stolen сохраняются."""
        projected = public_projection(SNAPSHOT)
        character = projected["characters"][0]
        self.assertEqual(character["skills_base"], {"Katanas": 20.5, "Toughness": 0.4, "Athletics": 1.0000001})
        self.assertNotIn("hidden_stats_raw", character)
        self.assertEqual(character["items"], [{"item_sid": "a", "is_stolen": True}])
        self.assertEqual(character["carrying"], {"is_carrying": False})
        self.assertEqual(projected["property"]["buildings"][0]["interior"], [{"x": 2}])
        self.assertIn("raw", SNAPSHOT["characters"][0]["items"][0])  # исходные данные не изменяются

    def test_start_set_limit(self) -> None:
        """Превышение лимита стартового набора (manifest, brief, squad, changes) даёт ошибку публикации."""
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "manifest.json").write_text("{}", encoding="utf-8")
            (package / "brief.md").write_text("x" * (START_SET_LIMIT_CHARS - 2), encoding="utf-8")
            validate_start_set_size(package, {"brief": "brief.md"})
            (package / "brief.md").write_text("x" * (START_SET_LIMIT_CHARS - 1), encoding="utf-8")
            with self.assertRaises(PublicationValidationError):
                validate_start_set_size(package, {"brief": "brief.md"})

    def test_manifest_schema_current_index_is_optional_and_deprecated(self) -> None:
        """Манифест без current_index валиден; манифест прежнего выпуска с current_index остаётся валидным."""
        schema = json.loads((ROOT / "contracts/manifest.schema.json").read_text(encoding="utf-8"))
        artifacts = schema["properties"]["artifacts"]
        self.assertNotIn("current_index", artifacts["required"])
        self.assertTrue(artifacts["properties"]["current_index"]["deprecated"])
        Draft202012Validator.check_schema(schema)

    def test_delivery_removes_legacy_current_dir_and_keeps_rest(self) -> None:
        """Доставка удаляет current/ и корневой current.json прежних выпусков, переносит extended/, не трогает Campaign/."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, target = root / "src", root / "companion"
            _make_bundle(src)
            published = target / "published"
            (published / "current/characters").mkdir(parents=True)
            (published / "current/characters/ps_-9.json").write_bytes(b"old")
            (published / "current/index.json").write_bytes(b"old")
            (published / "current.json").write_bytes(b"legacy monolith")
            (target / "Campaign").mkdir()
            (target / "Campaign/keep.md").write_text("canon", encoding="utf-8")

            deliver_to_companion(published_dir=src, companion_dir=target, auto_commit=False)

            self.assertFalse((published / "current.json").exists())
            self.assertFalse((published / "current").exists())
            self.assertTrue((published / "extended/current.json").is_file())
            self.assertEqual((target / "Campaign/keep.md").read_text(encoding="utf-8"), "canon")

    def test_delivery_does_not_follow_junctions(self) -> None:
        """Junction внутри устаревшего целевого current/ не обходится: файлы за его пределами остаются нетронутыми."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, target, outside = root / "src", root / "companion", root / "outside"
            _make_bundle(src)
            outside.mkdir()
            (outside / "precious.txt").write_text("keep", encoding="utf-8")
            (target / "published/current").mkdir(parents=True)
            link = target / "published/current/link"
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if made.returncode != 0:
                self.skipTest("Создание junction недоступно: отсутствует поддержка в окружении")

            deliver_to_companion(published_dir=src, companion_dir=target, auto_commit=False)

            self.assertEqual((outside / "precious.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
