"""Проверка проектных схем реестра локаций и ручных свидетельств сущностей."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import ValidationError

from tools.io_utils import ExtractionError
from tools.validation import load_entities_progress, validate, validate_entities_progress


class LocationRegistryValidationTests(unittest.TestCase):
    def test_template_faction_name_ru_is_optional_and_nullable(self) -> None:
        registry = {
            "format": "kenshi-location-registry/3", "campaign_id": "campaign", "snapshot_id": "head",
            "world_ref_id": "world", "game_time": "Day 13 08:00",
            "locations": [{
                "instance_id": "town", "name_ru": None, "name_en": "Town", "kind": "town",
                "region": "Greenbeach", "region_ru": "Зелёный пляж", "x": 0, "z": 0, "is_secret": None, "is_public": None,
                "unexplored_name": None, "template_faction": {"sid": "faction", "name_en": "Faction"},
            }],
        }
        for fields, valid in [
            ({}, True), ({"name_ru": None}, True), ({"name_ru": "Фракция"}, True),
            ({"name_ru": 1}, False), ({"name_ru": False}, False), ({"unexpected": "value"}, False),
        ]:
            with self.subTest(fields=fields):
                value = deepcopy(registry)
                value["locations"][0]["template_faction"].update(fields)
                if valid:
                    validate(value, "location-registry")
                else:
                    with self.assertRaises(ValidationError):
                        validate(value, "location-registry")


class EntitiesProgressValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / "examples" / "entities-progress.example.json"
        self.raw = path.read_bytes()
        self.example = json.loads(self.raw)

    def test_valid_documents_and_loading(self) -> None:
        optional_fields = deepcopy(self.example)
        optional_fields["world_ref_id"] = "world"
        optional_fields["entities"]["52306-rebirth.mod"]["game_time"] = "Day 0 00:00"
        optional_fields["entities"]["50519-rebirth.mod"]["game_time"] = "Day 100 23:59"
        for value in (self.example, optional_fields, {
            "format": "kenshi-entities-progress/1", "campaign_id": "hot-lon", "entities": {},
        }):
            with self.subTest(value=value):
                original = deepcopy(value)
                self.assertEqual(validate_entities_progress(value), original)
                self.assertEqual(value, original)
                for raw in (json.dumps(value), json.dumps(value).encode("utf-8")):
                    self.assertEqual(load_entities_progress(raw), original)

    def test_invalid_fields_are_rejected_by_validator_and_loader(self) -> None:
        for scope, field, replacement in [
            ("root", "format", "kenshi-entities-progress/2"),
            ("root", "format", None),
            ("root", "campaign_id", None),
            ("root", "campaign_id", ""),
            ("root", "world_ref_id", ""),
            ("root", "world_ref_id", 1),
            ("root", "entities", None),
            ("root", "entities", []),
            ("root", "unexpected", True),
            ("entity", "outcome", None),
            ("entity", "evidence", None),
            ("entity", "outcome", ""),
            ("entity", "outcome", 1),
            ("entity", "evidence", ""),
            ("entity", "evidence", " \t\n"),
            ("entity", "evidence", 1),
            ("entity", "game_time", "Day 1 24:00"),
            ("entity", "game_time", "Day 1 12:60"),
            ("entity", "game_time", "Day 1 1:00"),
            ("entity", "game_time", "Day -1 12:00"),
            ("entity", "game_time", 1),
            ("entity", "event_id", "event"),
        ]:
            with self.subTest(scope=scope, field=field, replacement=replacement):
                value = deepcopy(self.example)
                target = value if scope == "root" else value["entities"]["50519-rebirth.mod"]
                if replacement is None:
                    del target[field]
                else:
                    target[field] = replacement
                with self.assertRaises(ValidationError):
                    validate_entities_progress(value)
                with self.assertRaises(ValidationError):
                    load_entities_progress(json.dumps(value))

    def test_invalid_sids_and_ambiguous_json_are_rejected(self) -> None:
        for sid in ("", "boss sid", "boss/sid", "босс"):
            with self.subTest(sid=sid):
                value = deepcopy(self.example)
                value["entities"] = {sid: value["entities"]["50519-rebirth.mod"]}
                with self.assertRaises(ValidationError):
                    validate_entities_progress(value)
        with self.assertRaises(ExtractionError):
            load_entities_progress('{"entities": {}, "entities": {}}')
