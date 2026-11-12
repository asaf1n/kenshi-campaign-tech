"""Регрессии графа размещения, учёта экземпляров и подтверждённых отметок."""
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch
from typing import Any

from tools.unique_entities_registry import (
    GRADE_SID, MANUFACTURER_SID, apply_progress_to_unique_entities, build_unique_entities_registry,
)
from tools.validation import validate_unique_entities_registry


# SID и названия сверены с локальным кэшем GameDB v14. Связи ниже — изолированный сценарий.
WEAPONS = (
    ("1020-gamedata.base", "Wakizashi"), ("474-gamedata.base", "Topper"),
    ("475-gamedata.base", "Horse Chopper"), ("476-gamedata.base", "Katana"),
    ("477-gamedata.base", "Fragment Axe"), ("52287-rebirth.mod", "Holed Sabre"),
    ("52290-rebirth.mod", "Heavy Jitte"), ("52292-rebirth.mod", "Combat Cleaver"),
    ("52299-rebirth.mod", "Paladin's Cross"), ("52300-rebirth.mod", "Moon Cleaver"),
    ("52301-rebirth.mod", "Polearm"), ("52302-rebirth.mod", "Staff"),
    ("52303-rebirth.mod", "Jitte"), ("52304-rebirth.mod", "Foreign Sabre"),
    ("52305-rebirth.mod", "Desert Sabre"), ("52306-rebirth.mod", "Falling Sun"),
    ("52307-rebirth.mod", "Heavy Polearm"), ("52308-rebirth.mod", "Naginata"),
    ("52309-rebirth.mod", "Naginata Katana"), ("56728-Dialogue.mod", "Exile Plank"),
    ("65260-rebirth.mod", "Short-Cleaver"), ("901-gamedata.base", "Longsword"),
    ("903-gamedata.base", "Ringed Sabre"), ("922-gamedata.base", "Nodachi"),
    ("923-gamedata.base", "Guardless Katana"), ("924-gamedata.base", "Ninja Blade"),
)
BURN, WIZARD, PONK, SCREAMER = "50570-rebirth.mod", "50519-rebirth.mod", "96407-rebirth.mod", "96409-rebirth.mod"


def _ref(sid: str) -> list[list[Any]]:
    return [[sid, [1, 0, 0]]]


def _item(sid: str, base_sid: str, section: str = "main", container: str = "inventory") -> dict[str, Any]:
    return {"item_sid": sid, "base_data_sid": base_sid, "section": section, "container": container,
            "material_sid": GRADE_SID, "company_sid": MANUFACTURER_SID}


class UniqueEntitiesRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cur: dict[str, Any] = {
            "snapshot_id": "snapshot", "campaign_id": "campaign", "generated_at": "2026-11-11T00:00:00Z",
            "world": {"day": 136, "time": "20:21"}, "squads": [],
            "world_state": {"unique_npcs": [
                {"sid": BURN, "handle": [1, 2, 3, 4, 5], "state_code": 1, "player": False},
                {"sid": WIZARD, "handle": [1, 2, 3, 4, 6], "state_code": 0, "player": True},
                {"sid": "dynamic-only", "name": "Unknown", "handle": [1, None, 3, 4, 5]},
            ]},
            "characters": [{"id": "ps:burn", "name": "Ожог", "handle": [1, 2, 3, 4, 5],
                            "squad_name": "Squad", "items": []}],
        }
        characters = [
            {"sid": BURN, "name": "Burn", "bool": {"unique": True}, "int": {"NPC class": 9}},
            {"sid": WIZARD, "name": "Cannibal Grand Wizard", "bool": {"unique": True},
             "int": {"bounty amount": 40000}},
            {"sid": PONK, "name": "Ponk", "bool": {"unique": True}, "int": {"bounty amount": 20000}},
            {"sid": SCREAMER, "name": "Screamer the False", "bool": {"unique": True}},
            {"sid": "captive", "name": "Captive", "bool": {"unique": True}},
        ]
        squads = [
            {"sid": "50569-rebirth.mod", "refs": {"leader": _ref(BURN)}},
            {"sid": "50521-rebirth.mod", "refs": {"squad": _ref(WIZARD)}},
            {"sid": "96404-rebirth.mod", "refs": {"squad2": _ref(PONK), "leader": _ref(SCREAMER),
                                                     "prisoners": _ref("prisoner-squad")}},
            {"sid": "prisoner-squad", "refs": {"leader": _ref("captive"),
                                                 "prisoners": _ref("96404-rebirth.mod")}},
        ]
        towns = []
        locations = []
        for template, name, squad, instance in (
            ("48447-rebirth.mod", "Burn's Tower", "50569-rebirth.mod", "6-rebirth-INGAME"),
            ("50522-rebirth.mod", "Cannibal Capital", "50521-rebirth.mod", "26-rebirth-INGAME"),
            ("95726-rebirth.mod", "Tower of Abuse", "96404-rebirth.mod", "abuse-instance"),
        ):
            towns.append({"sid": template, "name": name, "refs": {"residents": _ref(squad)}})
            locations.append({"instance_id": instance, "template_sid": template, "name_en": name,
                              "name_ru": None, "region": "region"})
        # Отдельные владельцы позволяют проверять полноту каталога без локальной установки игры.
        for number, (sid, _) in enumerate(WEAPONS):
            characters.append({"sid": f"holder-{number:02}", "name": f"Holder {number}",
                               "refs": {"weapon level": _ref(MANUFACTURER_SID), "weapons": _ref(sid)}})
        characters[-1]["refs"]["weapons"] += _ref(WEAPONS[0][0])
        self.records = {"units": {"characters": characters, "squads": squads, "towns": towns},
                        "reactions": {"world_states": [{"sid": "state", "name": "Screamer alive", "type_id": 110,
                                                         "refs": {"NPC is": _ref(SCREAMER)}}]}}
        self.index = {sid: [name, None, "WEAPON", "rebirth.mod", {"skill category": 1}]
                      for sid, name in WEAPONS}
        self.world = {"world_ref_id": "world", "created_at": "2026-11-10T00:00:00Z", "locations": locations}

    def build(self, **kwargs: Any) -> dict[str, Any]:
        return build_unique_entities_registry(self.cur, self.world, self.records, self.index, **kwargs)

    def test_meitou_russian_names_from_po_localization(self) -> None:
        """Проверяет разрешение русских названий Мейто через gamedb.load_po() и схему."""
        expected = dict(zip((sid for sid, _ in WEAPONS), (
            "Вакидзаси", "Топпер", "Конеруб", "Катана", "Составной топор",
            "Сабля с отверстием", "Тяжелый дзюттэ", "Боевой тесак", "Крест паладина",
            "Лунный тесак", "Глефа", "Посох", "Дзюттэ", "Иноземная сабля",
            "Пустынная сабля", "Падающее солнце", "Бердыш", "Нагината", "Нагината катана",
            "Доска изгнанника", "Короткий тесак", "Меч", "Сабля с кольцами", "Нодати",
            "Катана без цубы", "Клинок ниндзя",
        ), strict=True))
        # 1. При наличии данных локализации PO и английских имён в индексе все 26 Мейто разрешаются в name_ru
        registry = self.build()
        weapons = registry["meitou_weapons"]
        self.assertEqual(len(weapons), 26)
        self.assertEqual({weapon["base_sid"] for weapon in weapons}, set(expected))
        for weapon in weapons:
            self.assertEqual(weapon["name_ru"], expected[weapon["base_sid"]])
            self.assertRegex(weapon["name_ru"], r"[А-Яа-яЁё]")
            self.assertNotIn("weapon_name_ru", weapon)
        validate_unique_entities_registry(registry)

        # 2. Если локализация отсутствует, name_ru остаётся None
        with patch("tools.unique_entities_registry.gamedb.load_po", return_value=({}, {}, {})):
            no_po_registry = self.build()
            for weapon in no_po_registry["meitou_weapons"]:
                self.assertIsNone(weapon["name_ru"])
            validate_unique_entities_registry(no_po_registry)

        # 3. Совместимость со схемами версий 1 и 2
        for version in ("1", "2"):
            compatible = {**registry, "format": f"kenshi-unique-entities-registry/{version}",
                          "unique_npcs": [], "bosses": [], "meitou_weapons": deepcopy(weapons)}
            if version == "1":
                for weapon in compatible["meitou_weapons"]:
                    weapon.pop("observation_status")
                    weapon["provenance_status"] = "observed_in_inventory"
            validate_unique_entities_registry(compatible)

    def test_schema_graph_dynamic_and_determinism(self) -> None:
        original = deepcopy((self.cur, self.world, self.records, self.index))
        registry = self.build()
        validate_unique_entities_registry(registry)
        self.assertEqual(registry, self.build())
        self.assertEqual(original, (self.cur, self.world, self.records, self.index))
        npcs = {row["npc_sid"]: row for row in registry["unique_npcs"]}
        self.assertEqual(npcs[BURN]["category"], "recruit")
        self.assertEqual(npcs[BURN]["locations"][0]["source_chain"], [
            f"CHARACTER:{BURN}", "SQUAD:50569-rebirth.mod", "TOWN:48447-rebirth.mod", "INSTANCE:6-rebirth-INGAME"])
        self.assertEqual(npcs[BURN]["dynamic"]["squad_status"], "confirmed_in_squad")
        self.assertEqual(npcs[BURN]["dynamic"]["character_id"], "ps:burn")
        self.assertFalse(npcs[BURN]["dynamic"]["player_defeat_flag"])
        self.assertEqual(npcs[WIZARD]["dynamic"]["squad_status"], "confirmed_not_in_squad")
        self.assertTrue(npcs[WIZARD]["dynamic"]["player_defeat_flag"])
        for sid in (PONK, SCREAMER, "captive"):
            self.assertEqual(npcs[sid]["locations"][0]["instance_id"], "abuse-instance")
        weapons = registry["meitou_weapons"]
        self.assertEqual([row["base_sid"] for row in weapons], sorted(sid for sid, _ in WEAPONS))
        self.assertEqual(len(weapons[0]["static_holders"]), 2)
        for invalid in (None, [], [0] * 5, [1, None, 3, 4, 5], [True, 2, 3, 4, 5], [1] * 6):
            with self.subTest(handle=invalid):
                self.cur["world_state"]["unique_npcs"][0]["handle"] = invalid
                row = next(row for row in self.build()["unique_npcs"] if row["npc_sid"] == BURN)
                self.assertEqual(row["dynamic"]["squad_status"], "unresolved")
                self.assertIsNone(row["dynamic"]["handle"])
        self.cur = deepcopy(original[0])
        for rows in self.records["units"].values():
            rows.reverse()
        self.world["locations"].reverse()
        self.cur["world_state"]["unique_npcs"].reverse()
        self.assertEqual(registry, self.build())
        for field, kind in (("bar squads", "bar_squad"), ("roaming squads", "roaming")):
            with self.subTest(town_link=field):
                town = next(row for row in self.records["units"]["towns"] if row["sid"] == "48447-rebirth.mod")
                town["refs"] = {field: _ref("50569-rebirth.mod")}
                npc = next(row for row in self.build()["unique_npcs"] if row["npc_sid"] == BURN)
                self.assertEqual(npc["locations"][0]["link_type"], kind)
        town["refs"] = {"residents": _ref("50569-rebirth.mod")}
        self.world["locations"].append({**self.world["locations"][-1], "instance_id": "burn-second"})
        row = next(row for row in self.build()["unique_npcs"] if row["npc_sid"] == BURN)
        self.assertEqual([link["resolution"] for link in row["locations"]], ["ambiguous"] * 2)
        self.world["locations"] = []
        row = next(row for row in self.build()["unique_npcs"] if row["npc_sid"] == BURN)
        self.assertEqual(row["locations"][0]["resolution"], "unresolved")
        empty = build_unique_entities_registry(self.cur, self.world, {}, {})
        validate_unique_entities_registry(empty)
        self.assertEqual(empty["meitou_weapons"], [])

    def test_instances_and_trophy_protocol(self) -> None:
        """Проверяет учёт экземпляров Мейто и подтверждение трофея исключительно через entities_progress."""
        sid = WEAPONS[0][0]
        self.cur["characters"][0]["items"] = [
            _item("equipped", sid, "back"), _item("spare", sid),
            _item("packed", sid, container="inventory>bag"),
            {**_item("wrong-grade", sid), "material_sid": "other"},
            {**_item("wrong-maker", sid), "company_sid": "other"},
        ]
        self.cur["property"] = {"buildings": [{"id": "house", "zone": "1.2", "town": "Home", "name": "House",
                                              "interior": [{"id": "chest", "name": "Chest", "storage_items": [
                                                  _item("stored", sid)]}]}]}
        confirmed_progress = {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {sid: {"outcome": "trophy_claimed", "evidence": "session-note",
                               "game_time": "Day 136 20:20"}},
        }
        empty_progress = {"format": "kenshi-entities-progress/1", "campaign_id": "campaign", "entities": {}}
        for progress, confirmed in ((None, False), (empty_progress, False), (confirmed_progress, True)):
            with self.subTest(confirmed=confirmed):
                registry = self.build(entities_progress_data=progress)
                weapon = registry["meitou_weapons"][0]
                self.assertEqual(weapon["observation_status"], "observed")
                self.assertEqual(weapon["provenance_status"],
                                 "confirmed_trophy" if confirmed else "unconfirmed")
                self.assertEqual(weapon["trophy_evidence"] is not None, confirmed)
                self.assertEqual({row["location_kind"] for row in weapon["observed_instances"]},
                                 {"equipped", "inventory", "backpack", "storage"})
                self.assertEqual(len(weapon["observed_instances"]), 4)
                stored = next(row for row in weapon["observed_instances"] if row["location_kind"] == "storage")
                self.assertEqual(stored["item_sid"], "stored")
                self.assertEqual(stored["storage_settlement"], "Home")
                if confirmed:
                    self.assertEqual(weapon["trophy_evidence"], {
                        "outcome": "trophy_claimed", "evidence": "session-note", "game_time": "Day 136 20:20"})
        before = self.build(entities_progress_data=confirmed_progress)
        self.cur["characters"][0]["items"].reverse()
        self.assertEqual(before, self.build(entities_progress_data=confirmed_progress))
        self.assertEqual(confirmed_progress, {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {sid: {"outcome": "trophy_claimed", "evidence": "session-note",
                               "game_time": "Day 136 20:20"}},
        })

    def test_clearance_requires_applicable_manual_mark(self) -> None:
        registry = self.build()
        bosses = {row["npc_sid"]: row for row in registry["bosses"]}
        self.assertEqual(bosses[WIZARD]["observed_state"]["state_code"], 0)
        self.assertTrue(all(row["cleared_evidence"] is None for row in bosses.values()))
        self.assertEqual(bosses[WIZARD]["classification_origin"], "game_bounty")
        self.assertEqual(bosses[SCREAMER]["classification_origin"], "world_state")
        self.assertEqual(bosses["holder-00"]["classification_origin"], "meitou_holder")
        self.records["units"]["characters"][2].pop("int")
        bosses = {row["npc_sid"]: row for row in self.build()["bosses"]}
        self.assertEqual(bosses[PONK]["classification_origin"], "custom_catalog")
        progress = {"format": "kenshi-location-progress/3", "campaign_id": "campaign", "world_ref_id": "world",
                    "locations": [{"instance_id": "26-rebirth-INGAME", "evidence": "session-note",
                                   "game_time": "Day 136 20:20"}]}
        for campaign, time, expected in (("campaign", "Day 136 20:20", True),
                                         ("other", "Day 136 20:20", False),
                                         ("campaign", "Day 137 00:00", False)):
            with self.subTest(campaign=campaign, time=time):
                progress["campaign_id"] = campaign
                progress["locations"][0]["game_time"] = time
                # Без ручного слоя entities_progress: босс не получает personal/cleared_evidence,
                # но его static_locations получают отметку зачистки по location-progress
                reg_no_p = self.build(progress_data=progress)
                bosses_no_p = {row["npc_sid"]: row for row in reg_no_p["bosses"]}
                self.assertIsNone(bosses_no_p[WIZARD]["cleared_evidence"])
                loc_evidence = bosses_no_p[WIZARD]["static_locations"][0]["cleared_evidence"]
                self.assertEqual(loc_evidence is not None, expected)
                if expected:
                    self.assertEqual(loc_evidence, {
                        "location_instance_id": "26-rebirth-INGAME", "cleared_at": time, "source": "session-note"})

        # Личное подтверждение победы босса идёт исключительно через entities_progress
        # и не зависит от применимости location-progress (campaign_id/game_time)
        entities_progress = {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {WIZARD: {"outcome": "dead", "evidence": "session-note", "game_time": "Day 136 20:20"}},
        }
        reg_personal = self.build(progress_data=progress, entities_progress_data=entities_progress)
        boss_personal = next(row for row in reg_personal["bosses"] if row["npc_sid"] == WIZARD)
        self.assertEqual(boss_personal["personal_evidence"], {
            "outcome": "dead", "evidence": "session-note", "game_time": "Day 136 20:20"})
        self.assertEqual(boss_personal["cleared_evidence"], {
            "location_instance_id": None, "cleared_at": "Day 136 20:20", "source": "session-note"})

        # Исход, не означающий победу/захват, не формирует cleared_evidence босса
        spotted_progress = {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {WIZARD: {"outcome": "spotted", "evidence": "session-note"}},
        }
        boss_spotted = next(row for row in self.build(entities_progress_data=spotted_progress)["bosses"]
                            if row["npc_sid"] == WIZARD)
        self.assertIsNotNone(boss_spotted["personal_evidence"])
        self.assertIsNone(boss_spotted["cleared_evidence"])

    def test_apply_progress_to_unique_entities(self) -> None:
        """Проверяет наложение location-progress и entities_progress на уже построенный реестр."""
        meitou_sid = WEAPONS[0][0]
        registry = self.build()
        progress = {
            "format": "kenshi-location-progress/3", "campaign_id": "campaign", "world_ref_id": "world",
            "locations": [{"instance_id": "26-rebirth-INGAME", "evidence": "session-note", "game_time": "Day 136 20:20"}],
        }
        entities_progress = {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {
                WIZARD: {"outcome": "dead", "evidence": "session-note", "game_time": "Day 136 20:20"},
                meitou_sid: {"outcome": "trophy_claimed", "evidence": "session-note"},
            },
        }
        # 1. Применение прогресса с world_ref
        enriched = apply_progress_to_unique_entities(registry, progress, self.world, entities_progress)
        validate_unique_entities_registry(enriched)
        weapon = next(w for w in enriched["meitou_weapons"] if w["base_sid"] == meitou_sid)
        self.assertEqual(weapon["provenance_status"], "confirmed_trophy")
        self.assertEqual(weapon["trophy_evidence"], {
            "outcome": "trophy_claimed", "evidence": "session-note", "game_time": None,
        })
        bosses = {b["npc_sid"]: b for b in enriched["bosses"]}
        self.assertEqual(bosses[WIZARD]["personal_evidence"], {
            "outcome": "dead", "evidence": "session-note", "game_time": "Day 136 20:20",
        })
        self.assertEqual(bosses[WIZARD]["cleared_evidence"], {
            "location_instance_id": None, "cleared_at": "Day 136 20:20", "source": "session-note",
        })
        self.assertEqual(bosses[WIZARD]["static_locations"][0]["cleared_evidence"], {
            "location_instance_id": "26-rebirth-INGAME", "cleared_at": "Day 136 20:20", "source": "session-note",
        })
        # Исходный реестр не изменился (иммутабельность)
        original_wizard = next(b for b in registry["bosses"] if b["npc_sid"] == WIZARD)
        self.assertIsNone(original_wizard["cleared_evidence"])
        self.assertIsNone(original_wizard["personal_evidence"])

        # 2. Применение прогресса без world_ref (сбор instance_id из сущностей), entities_progress не задан
        enriched_no_wref = apply_progress_to_unique_entities(registry, progress, world_ref=None)
        validate_unique_entities_registry(enriched_no_wref)
        bosses_no_wref = {b["npc_sid"]: b for b in enriched_no_wref["bosses"]}
        self.assertEqual(bosses_no_wref[WIZARD]["static_locations"][0]["cleared_evidence"], {
            "location_instance_id": "26-rebirth-INGAME", "cleared_at": "Day 136 20:20", "source": "session-note",
        })
        self.assertIsNone(bosses_no_wref[WIZARD]["personal_evidence"])
        self.assertIsNone(bosses_no_wref[WIZARD]["cleared_evidence"])

        # 3. Сброс отметок при progress_data и entities_progress_data is None
        reset = apply_progress_to_unique_entities(enriched, None)
        validate_unique_entities_registry(reset)
        self.assertTrue(all(b["cleared_evidence"] is None for b in reset["bosses"]))
        self.assertTrue(all(b["personal_evidence"] is None for b in reset["bosses"]))
        self.assertTrue(all(link["cleared_evidence"] is None for b in reset["bosses"] for link in b["static_locations"]))
        self.assertTrue(all(w["provenance_status"] == "unconfirmed" for w in reset["meitou_weapons"]))

        # Входные progress/entities_progress не мутируются
        self.assertEqual(progress["locations"][0]["instance_id"], "26-rebirth-INGAME")
        self.assertEqual(entities_progress["entities"][WIZARD]["outcome"], "dead")

    def test_agnu_remediation_and_categories_and_multi_locations(self) -> None:
        """Проверяет Агну в отряде, классификацию категорий и независимость 1:N локаций."""
        AGNU = "97095-rebirth.mod"
        TOWER_ABUSE = "108-rebirth-INGAME"
        # Настройка Агну: NPC class 9, в world_states, в отряде игрока (state_code 1, confirmed_in_squad)
        self.cur["world_state"]["unique_npcs"].append({
            "sid": AGNU, "handle": [1, 5, 5, 5, 5], "state_code": 1, "player": False,
        })
        self.cur["characters"].append({
            "id": "ps:agnu", "name": "Агну", "handle": [1, 5, 5, 5, 5], "squad_name": "Squad", "items": [],
        })
        self.records["units"]["characters"].append({
            "sid": AGNU, "name": "Agnu", "bool": {"unique": True}, "int": {"NPC class": 9},
        })
        self.records["units"]["squads"].append({
            "sid": "agnu-squad", "refs": {"leader": _ref(AGNU)},
        })
        self.records["units"]["towns"].append({
            "sid": "tower-town", "name": "Tower of Abuse", "refs": {"residents": _ref("agnu-squad")},
        })
        self.world["locations"].append({
            "instance_id": TOWER_ABUSE, "template_sid": "tower-town", "name_en": "Tower of Abuse",
            "name_ru": "Башня Жестокости", "region": "Venge",
        })
        self.records["reactions"]["world_states"].append({
            "sid": "agnu-state", "name": "Agnu rescued", "type_id": 110, "refs": {"NPC is": _ref(AGNU)},
        })

        MINOR = "minor-bounty-npc"
        self.records["units"]["characters"].append({
            "sid": MINOR, "name": "Minor Bounty", "bool": {"unique": True}, "int": {"bounty amount": 5000},
        })
        LEADER = "faction-leader"
        self.records["units"]["characters"].append({
            "sid": LEADER, "name": "Leader", "bool": {"unique": True}, "int": {"NPC class": 5, "bounty amount": 50000},
        })

        progress = {
            "format": "kenshi-location-progress/3", "campaign_id": "campaign", "world_ref_id": "world",
            "locations": [{"instance_id": TOWER_ABUSE, "evidence": "session-tower", "game_time": "Day 136 20:20"}],
        }

        registry = self.build(progress_data=progress)
        validate_unique_entities_registry(registry)

        npcs = {r["npc_sid"]: r for r in registry["unique_npcs"]}
        bosses = {r["npc_sid"]: r for r in registry["bosses"]}

        # 1. Агну: category == 'recruit' (NPC class 9 приоритетнее world_state), в отряде
        self.assertEqual(npcs[AGNU]["category"], "recruit")
        self.assertEqual(npcs[AGNU]["dynamic"]["squad_status"], "confirmed_in_squad")
        self.assertEqual(npcs[AGNU]["dynamic"]["character_id"], "ps:agnu")
        # Связь с Башней Жестокости сохранена и содержит cleared_evidence
        self.assertEqual(npcs[AGNU]["locations"][0]["instance_id"], TOWER_ABUSE)
        self.assertIsNotNone(npcs[AGNU]["locations"][0]["cleared_evidence"])
        self.assertEqual(npcs[AGNU]["locations"][0]["cleared_evidence"]["location_instance_id"], TOWER_ABUSE)
        # Но персональное свидетельство поражения отсутствует!
        self.assertIsNone(npcs[AGNU]["personal_evidence"])
        if AGNU in bosses:
            self.assertEqual(bosses[AGNU]["category"], "recruit")
            self.assertIsNone(bosses[AGNU]["cleared_evidence"])
            self.assertIsNone(bosses[AGNU]["personal_evidence"])

        # 2. Категории: minor bounty -> significant_npc, leader -> world_leader
        self.assertEqual(npcs[MINOR]["category"], "significant_npc")
        self.assertEqual(npcs[LEADER]["category"], "world_leader")
        self.assertEqual(bosses[LEADER]["category"], "world_leader")

    def test_meitou_orthogonal_statuses(self) -> None:
        """Проверяет независимость observation_status и provenance_status для оружия Мейто."""
        sid = WEAPONS[0][0]
        progress = {
            "format": "kenshi-entities-progress/1", "campaign_id": "campaign",
            "entities": {sid: {"outcome": "trophy_claimed", "evidence": "session-note"}},
        }
        self.cur["characters"][0]["items"] = []
        reg = self.build(entities_progress_data=progress)
        validate_unique_entities_registry(reg)
        w = next(item for item in reg["meitou_weapons"] if item["base_sid"] == sid)
        self.assertEqual(w["observation_status"], "not_observed")
        self.assertEqual(w["provenance_status"], "confirmed_trophy")
        self.assertEqual(len(w["observed_instances"]), 0)

        cross_sid = "52299-rebirth.mod"
        self.cur["characters"][0]["items"] = [_item("equipped-cross", cross_sid, "back")]
        reg2 = self.build()
        validate_unique_entities_registry(reg2)
        w_cross = next(item for item in reg2["meitou_weapons"] if item["base_sid"] == cross_sid)
        self.assertEqual(w_cross["observation_status"], "observed")
        self.assertEqual(w_cross["provenance_status"], "unconfirmed")
        self.assertEqual(len(w_cross["observed_instances"]), 1)
        self.assertIsNone(w_cross["trophy_evidence"])


if __name__ == "__main__":
    unittest.main()
