"""Проверка локализованных полей реестра локаций по проектной схеме."""
from copy import deepcopy
import unittest

from jsonschema import ValidationError

from tools.validation import validate


class LocationRegistryValidationTests(unittest.TestCase):
    def test_template_faction_name_ru_is_optional_and_nullable(self) -> None:
        registry = {
            "format": "kenshi-location-registry/3", "campaign_id": "campaign", "snapshot_id": "head",
            "world_ref_id": "world", "game_time": "Day 13 08:00",
            "locations": [{
                "instance_id": "town", "name_ru": None, "name_en": "Town", "kind": "town",
                "region": "Зелёный пляж", "x": 0, "z": 0, "is_secret": None, "is_public": None,
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
