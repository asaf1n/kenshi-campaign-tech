"""Публикационные слои снимка: очищенная проекция, шарды current/ и доставка в игровой репозиторий."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.current_layers import (
    INDEX_PATH,
    START_SET_LIMIT_CHARS,
    build_layers,
    public_projection,
    validate_layers,
)
from tools.deliver_companion import deliver_to_companion
from tools.io_utils import canonical
from tools.validation import PublicationValidationError

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
    "property": {
        "buildings": [{"id": "1-zone", "name": "Дом", "town": "Скуин", "interior": [{"raw": 1, "x": 2}]}],
        "storage_by_location": {"Скуин": {"total_items_count": 3}},
    },
    "world": {"day": 131}, "world_state": {"k": 1}, "diplomacy": {}, "research": {}, "supplies": {}, "squads": [],
}


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

    def test_layers_are_reproducible_from_monolith_and_validated(self) -> None:
        """Шарды воспроизводятся из монолита побайтово; подмена шарда, лишний файл и превышение лимита дают ошибку."""
        projected = public_projection(SNAPSHOT)
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            for rel_path, data in build_layers(projected).items():
                (package / rel_path).parent.mkdir(parents=True, exist_ok=True)
                (package / rel_path).write_bytes(data)
            (package / "manifest.json").write_text("{}", encoding="utf-8")
            artifacts = {"current_index": INDEX_PATH}

            validate_layers(package, artifacts, projected, PublicationValidationError)

            (package / "current/characters/ps_-5.json").write_bytes(canonical({"id": "ps:-5", "name": "Подмена"}))
            with self.assertRaises(PublicationValidationError):
                validate_layers(package, artifacts, projected, PublicationValidationError)
            (package / "current/characters/ps_-5.json").write_bytes(build_layers(projected)["current/characters/ps_-5.json"])

            (package / "current/extra.json").write_bytes(b"{}")
            with self.assertRaises(PublicationValidationError):
                validate_layers(package, artifacts, projected, PublicationValidationError)
            (package / "current/extra.json").unlink()

            (package / "brief.md").write_text("x" * (START_SET_LIMIT_CHARS + 1), encoding="utf-8")
            with self.assertRaises(PublicationValidationError):
                validate_layers(package, {**artifacts, "brief": "brief.md"}, projected, PublicationValidationError)

    def test_shard_names_are_safe_and_unique(self) -> None:
        """Небезопасные идентификаторы получают имя-хеш внутри current/, повтор идентификатора прерывает сборку."""
        unsafe = {"characters": [], "property": {"buildings": [
            {"id": "../evil", "name": "a"}, {"id": "con:x?", "name": "b"}, {"id": "ok-1", "name": "c"}, {"id": "NUL", "name": "d"}]}}
        paths = [entry["path"] for entry in json.loads(build_layers(unsafe)[INDEX_PATH])["bases"]]
        self.assertTrue(all(path.startswith("current/bases/") and ".." not in path and "?" not in path for path in paths))
        self.assertIn("current/bases/ok-1.json", paths)
        self.assertNotIn("current/bases/NUL.json", paths)
        for duplicated in ({"characters": [{"id": "ps:1"}, {"id": "ps:1"}]},
                           {"property": {"buildings": [{"id": "A"}, {"id": "A"}]}},
                           {"property": {"buildings": [{"id": "A"}, {"id": "a"}]}}):
            with self.subTest(duplicated=list(duplicated)), self.assertRaises(ValueError):
                build_layers(duplicated)

    def test_delivery_does_not_follow_junctions(self) -> None:
        """Junction внутри целевого current/ не обходится: файлы за его пределами остаются нетронутыми."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, target, outside = root / "src", root / "companion", root / "outside"
            for rel_path, data in build_layers(public_projection(SNAPSHOT)).items():
                (src / rel_path).parent.mkdir(parents=True, exist_ok=True)
                (src / rel_path).write_bytes(data)
            (src / "manifest.json").write_bytes(canonical({"validation": {"status": "passed"}}))
            outside.mkdir()
            (outside / "precious.txt").write_text("keep", encoding="utf-8")
            (target / "published/current").mkdir(parents=True)
            link = target / "published/current/link"
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if made.returncode != 0:
                self.skipTest("Создание junction недоступно: отсутствует поддержка в окружении")

            deliver_to_companion(published_dir=src, companion_dir=target, auto_commit=False)

            self.assertEqual((outside / "precious.txt").read_text(encoding="utf-8"), "keep")

    def test_delivery_syncs_layer_directories_and_drops_legacy_monolith(self) -> None:
        """Доставка переносит current/ и extended/, удаляет устаревшие шарды и корневой current.json прежних выпусков."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, target = root / "src", root / "companion"
            for rel_path, data in build_layers(public_projection(SNAPSHOT)).items():
                (src / rel_path).parent.mkdir(parents=True, exist_ok=True)
                (src / rel_path).write_bytes(data)
            (src / "extended").mkdir()
            (src / "extended/current.json").write_bytes(b"{}")
            (src / "manifest.json").write_bytes(canonical({"validation": {"status": "passed"}}))

            published = target / "published"
            (published / "current/characters").mkdir(parents=True)
            (published / "current/characters/ps_-9.json").write_bytes(b"old")
            (published / "current.json").write_bytes(b"legacy monolith")
            (target / "Campaign").mkdir()
            (target / "Campaign/keep.md").write_text("canon", encoding="utf-8")

            deliver_to_companion(published_dir=src, companion_dir=target, auto_commit=False)

            self.assertFalse((published / "current.json").exists())
            self.assertFalse((published / "current/characters/ps_-9.json").exists())
            self.assertTrue((published / "current/characters/ps_-5.json").is_file())
            self.assertTrue((published / "extended/current.json").is_file())
            self.assertEqual((target / "Campaign/keep.md").read_text(encoding="utf-8"), "canon")


if __name__ == "__main__":
    unittest.main()
