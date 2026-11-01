"""Контрактные примеры и отказы, которые smoke-check не проверяет."""
import copy
import unittest

from jsonschema import ValidationError
from tools.io_utils import read_json
from tools.validation import ROOT, check_examples, validate


class ContractTests(unittest.TestCase):
    def test_examples_and_legacy_shapes(self) -> None:
        self.assertEqual(check_examples(), 4)
        for name, schema in (("manifest", "manifest"), ("event", "timeline-event")):
            value = read_json((ROOT / "examples" / f"{name}.example.json").read_bytes())
            for key in ("snapshot_id", "parent_snapshot_id", "branch_id", "game_time", "world_ref_id"):
                if key != "snapshot_id" or name == "event":
                    value.pop(key, None)
            validate(value, schema)

    def test_negative_contracts(self) -> None:
        manifest = read_json((ROOT / "examples/manifest.example.json").read_bytes())
        correction = read_json((ROOT / "examples/correction.example.json").read_bytes())
        cases = [
            ("manifest", manifest | {"unexpected": True}),
            ("manifest", manifest | {"generated_at": "yesterday"}),
            ("manifest", manifest | {"parent_snapshot_id": 3}),
            ("manifest", manifest | {"world_ref_id": []}),
            ("timeline-event", {k: v for k, v in correction.items() if k != "corrects_event_id"}),
            ("timeline-event", correction | {"corrects_event_id": ""}),
            ("timeline-event", correction | {"snapshot_id": ""}),
        ]
        for path in ("../escape.json", "C:/private.json", "/absolute.json", "folder/../../escape.json"):
            bad = copy.deepcopy(manifest)
            bad["artifacts"]["locations"] = path
            cases.append(("manifest", bad))
        for schema, value in cases:
            with self.subTest(schema=schema, value=value), self.assertRaises(ValidationError):
                validate(value, schema)
