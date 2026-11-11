"""Контрактные примеры и отказы, которые smoke-check не проверяет."""
import copy
import unittest

from jsonschema import ValidationError
from tools.io_utils import read_json
from tools.validation import ROOT, check_examples, validate, validate_unique_entities_registry


class ContractTests(unittest.TestCase):
    def test_unique_entities_registry_example(self) -> None:
        value = read_json((ROOT / "examples/unique-entities-registry.example.json").read_bytes())
        validate_unique_entities_registry(value)

        unknown = copy.deepcopy(value)
        unknown["unique_npcs"][0]["dynamic"].update(
            state_code=None, player_defeat_flag=None, squad_status="unresolved",
            character_id=None, handle=None, snapshot_id=None, game_time=None,
        )
        unknown["bosses"][0]["bounty"] = None
        unknown["meitou_weapons"][0].update(
            provenance_status="observed_in_inventory", trophy_evidence=None,
        )
        validate_unique_entities_registry(unknown)

    def test_unique_entities_registry_rejects_invalid_contracts(self) -> None:
        value = read_json((ROOT / "examples/unique-entities-registry.example.json").read_bytes())
        cases = [
            ((), "unexpected", True),
            (("unique_npcs", 0, "dynamic"), "unexpected", True),
            (("meitou_weapons", 0, "observed_instances", 0), "unexpected", True),
            ((), "format", "kenshi-unique-entities-registry/2"),
            (("unique_npcs", 0, "dynamic"), "squad_status", "in_squad"),
        ]
        for handle in ([1, 2, 3, 4], [1, 2, 3, 4, 5, 6], [1, None, 3, 4, 5],
                       [True, 2, 3, 4, 5], [0, 0, 0, 0, 0]):
            cases.append((("unique_npcs", 0, "dynamic"), "handle", handle))
        for path, key, invalid in cases:
            bad = copy.deepcopy(value)
            target = bad
            for part in path:
                target = target[part]
            target[key] = invalid
            with self.subTest(path=path, key=key, value=invalid), self.assertRaises(ValidationError):
                validate_unique_entities_registry(bad)

    def test_examples_and_legacy_shapes(self) -> None:
        self.assertGreaterEqual(check_examples(), 9)
        for name, schema in (("manifest", "manifest"), ("event", "timeline-event")):
            value = read_json((ROOT / "examples" / f"{name}.example.json").read_bytes())
            for key in ("snapshot_id", "parent_snapshot_id", "game_time", "world_ref_id"):
                if key != "snapshot_id" or name == "event":
                    value.pop(key, None)
            validate(value, schema)

        session_11 = read_json((ROOT / "examples/session-11.example.json").read_bytes())
        validate(session_11, "session")

    def test_real_snapshots_validate_against_session_schema(self) -> None:
        """Схема session корректно валидирует реальный v10 снимок и v11 эталон."""
        v11_example = read_json((ROOT / "examples/session-11.example.json").read_bytes())
        validate(v11_example, "session")
        real = ROOT / "published/extended/current.json"
        # published/ не отслеживается в git: реальный выпуск проверяется только при его наличии.
        if not real.is_file():
            self.skipTest(f"нет реального выпуска {real}; проверен только эталон v11")
        validate(read_json(real.read_bytes()), "session")

    def test_negative_contracts(self) -> None:
        manifest = read_json((ROOT / "examples/manifest.example.json").read_bytes())
        correction = read_json((ROOT / "examples/correction.example.json").read_bytes())
        session = read_json((ROOT / "examples/session-11.example.json").read_bytes())
        cases = [
            ("manifest", manifest | {"unexpected": True}),
            ("manifest", manifest | {"generated_at": "yesterday"}),
            ("manifest", manifest | {"parent_snapshot_id": 3}),
            ("manifest", manifest | {"world_ref_id": []}),
            ("timeline-event", {k: v for k, v in correction.items() if k != "corrects_event_id"}),
            ("timeline-event", correction | {"corrects_event_id": ""}),
            ("timeline-event", correction | {"snapshot_id": ""}),
            ("session", session | {"format": "kenshi-session/9"}),
            ("session", session | {"format": "kenshi-session/12"}),
            ("session", {k: v for k, v in session.items() if k != "format"}),
            ("session", session | {"diplomacy": {"player_faction_sid": "p", "relations": [{"faction_sid": "f", "relation": 200.0}], "summary": {}}}),
            ("session", session | {"world_state": {"unique_npcs": [{"sid": "npc1"}], "town_overrides": [], "summary": {}}}),
            ("session", session | {"characters": [{"id": "ps:1", "carrying": {"target_handle": None}}]}),
            ("session", session | {"characters": [{"id": "ps:1", "bounties": {"entries": []}}]}),
            ("manifest", manifest | {"campaign_id": 123}),
            ("session", session | {"characters": [{"id": "ps:1", "items": "not-a-list"}]}),
        ]
        for path in ("../escape.json", "C:/private.json", "/absolute.json", "folder/../../escape.json"):
            bad = copy.deepcopy(manifest)
            bad["artifacts"]["locations"] = path
            cases.append(("manifest", bad))
        for schema, value in cases:
            with self.subTest(schema=schema, value=value), self.assertRaises(ValidationError):
                validate(value, schema)
