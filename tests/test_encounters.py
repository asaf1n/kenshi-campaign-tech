"""Справочник встреч docs/reference/encounters.md: сборка из мини-таблиц и включение в коммит доставки."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from tools.deliver_companion import deliver_to_companion
from tools.encounters import (
    ENCOUNTERS_TABLE_REQUIRED,
    REQUIRED_TABLES,
    build_encounters,
    build_region_encounters,
    region_encounters_table,
)


def _spawn(place: str, squad: str, v0: str, source: str = "biome_homeless", faction: str = "") -> dict[str, str]:
    return {"source": source, "place_sid": place, "place_name_en": place, "place_name_ru": place, "place_faction_sid": faction,
            "spawn_amount": "1.5" if source == "biome_homeless" else "", "squad_sid": squad, "squad_name": squad, "ref_v0": v0}


def _member(squad: str, role: str, name: str, v0: str, v1: str = "0", sid: str | None = None) -> dict[str, str]:
    m_sid = sid or name
    return {"squad_sid": squad, "squad_name": squad, "role": role, "member_sid": m_sid, "member_name": name,
            "member_type": "CHARACTER", "v0": v0, "v1": v1, "v2": "0"}


def _template(sid: str, faction: str = "Фракция", states: str = "", chars: tuple[str, str] = ("0", "0"), faction_sids: str = "") -> dict[str, str]:
    return {"sid": sid, "name_en": sid, "name_ru": sid, "faction_sids": faction_sids, "faction_names": faction, "world_state_sids": states,
            "num_random_chars": chars[0], "num_random_chars_max": chars[1]}


def _unit(sid: str, name: str = "", kind: str = "character", stat_attack: str = "", combat_stats: str = "",
          animal_strength: str = "", armour_grade: str = "") -> dict[str, str]:
    return {
        "sid": sid, "kind": kind, "name_en": name or sid, "name_ru": name or sid,
        "stat_attack": stat_attack, "combat_stats": combat_stats,
        "animal_strength": animal_strength, "armour_grade": armour_grade,
    }


def _gear(template_sid: str, category: str, ref_name: str, ref_type: str = "WEAPON") -> dict[str, str]:
    return {
        "template_sid": template_sid, "template_name": template_sid,
        "category": category, "ref_sid": ref_name, "ref_name": ref_name, "ref_type": ref_type,
        "v0": "1", "v1": "100", "v2": "0",
    }


def _showcase_tables() -> dict[str, list[dict[str, str]]]:
    spawns = [
        _spawn("Border Zone", "bandits", "60"),
        _spawn("Border Zone", "boss_solo", "20"),
        _spawn("Border Zone", "dogs", "20"),
        _spawn("The Hub", "guards", "1", source="town_roaming", faction="f_guards"),
    ]
    templates = [
        _template("bandits", faction="Бандиты", states="ws_dust_king", faction_sids="fac_bandits"),
        _template("boss_solo", faction="Одиночки", faction_sids="fac_solo"),
        _template("dogs", faction="Звери", faction_sids="fac_beasts"),
        _template("guards", faction="", chars=("2", "4"), faction_sids=""),
    ]
    members = [
        # bandits: leader + 3 regulars with stats 10, 20, 50, plus stub (0, 0)
        _member("bandits", "leader", "Главарь", "1", sid="u_bandit_leader"),
        _member("bandits", "squad", "Разбойни/кца1/", "2", "4", sid="u_b1"),
        _member("bandits", "squad", "Головорез", "1", "2", sid="u_b2"),
        _member("bandits", "squad2", "Снайпер", "1", "1", sid="u_b3"),
        _member("bandits", "squad", "Заглушка", "0", "0", sid="u_stub"),
        # boss_solo: only leader
        _member("boss_solo", "leader", "Босс", "1", sid="u_boss"),
        # dogs: no leader, only animals
        _member("dogs", "animals", "Пёс", "3", "6", sid="u_dog"),
        # guards: choosefrom list
        _member("guards", "choosefrom list", "Стражник А", "1", sid="u_g1"),
        _member("guards", "choosefrom list", "Стражник Б", "1", sid="u_g2"),
    ]
    units = [
        _unit("u_bandit_leader", "Главарь", stat_attack="45", armour_grade="2"),
        _unit("u_b1", "Разбойни/кца1/", stat_attack="10", armour_grade="1"),
        _unit("u_b2", "Головорез", stat_attack="20", armour_grade="1"),
        _unit("u_b3", "Снайпер", stat_attack="50", armour_grade="3"),
        _unit("u_stub", "Заглушка", stat_attack="99", armour_grade="5"),
        _unit("u_boss", "Босс", combat_stats="75", armour_grade="4"),
        _unit("u_dog", "Пёс", kind="animal", animal_strength="35"),
        _unit("u_g1", "Стражник А", stat_attack="30", armour_grade="2"),
        _unit("u_g2", "Стражник Б", stat_attack="35", armour_grade="2"),
    ]
    gear = [
        _gear("u_bandit_leader", "weapons", "Конеруб"),
        _gear("u_bandit_leader", "weapon level", "Скелеты-кузнецы", ref_type="WEAPON_MANUFACTURER"),
        _gear("u_bandit_leader", "clothing", "Кожанка торговца", ref_type="ARMOUR"),
        _gear("u_b1", "weapons", "Палка"),
        _gear("u_b2", "weapons", "Тесак"),
        _gear("u_b3", "crossbows", "Арбалет", ref_type="CROSSBOW"),
        _gear("u_boss", "weapons", "Мейто-катана"),
        _gear("u_boss", "clothing", "Латный доспех", ref_type="ARMOUR"),
        _gear("u_dog", "weapon", "_dog type"),
    ]
    return {
        "squad_spawns": spawns,
        "squad_templates": templates,
        "squad_members": members,
        "unit_templates": units,
        "unit_template_gear": gear,
        "faction_hostility": [
            {"faction_sid": "f_guards", "faction_name_en": "Guards", "faction_name_ru": "Стража",
             "default_relation": "0", "enemy_classification": "-10"},
        ],
        "faction_relations": [],
    }


def _tables() -> dict[str, list[dict[str, str]]]:
    # Сумма ref_v0 региона = 100: доли равны весам. «Малый» (2) и «Редкий» (1) < 3% и сворачиваются в «Прочие (2)» = 3%.
    spawns = [_spawn("Регион", "big", "60"), _spawn("Регион", "cond", "20"), _spawn("Регион", "pick", "17"),
              _spawn("Регион", "small", "2"), _spawn("Регион", "rare", "1"),
              _spawn("Город", "big", "1", "town_roaming", "f1"), _spawn("Город", "cond", "1", "town_roaming", "f1")]
    members = [
        _member("big", "leader", "Мечни/кца1/", "1"), _member("big", "squad", "Охранни/кца/", "3", "5"),
        _member("big", "squad2", "Главн/аяый1/", "2", "2"), _member("big", "squad", "Заглушка", "0", "0"),
        _member("cond", "squad", "Бежен/каец1/", "1", "9"),
        _member("pick", "choosefrom list", "А", "1"), _member("pick", "choosefrom list", "Б", "1"),
        _member("pick", "choosefrom list", "В", "1"),
        _member("small", "squad", "Один", "1"), _member("rare", "squad", "Один", "1"),
    ]
    templates = [_template("big"), _template("cond", states="ws1"), _template("pick", chars=("1", "2")),
                 _template("small"), _template("rare")]
    relations = [{"faction_sid": "f1", "faction_name": "Ф1", "kind": "relation", "other_sid": f"o{i}", "other_name": f"О{i}",
                  "value": str(v)} for i, v in enumerate((-100, 0, 50))]
    hostility = [{"faction_sid": "f1", "faction_name_en": "F1", "faction_name_ru": "Ф1", "default_relation": "-5",
                  "enemy_classification": "-10"}]
    return {"squad_spawns": spawns, "squad_templates": templates, "squad_members": members,
            "faction_relations": relations, "faction_hostility": hostility}


class EncountersTests(unittest.TestCase):
    def test_golden_mini_tables(self) -> None:
        """Ловит ошибки свёртки <3% в «Прочие (k)», знака ¹, гендерных форм, choosefrom, заглушек v0=v1=0 и порядка."""
        text = build_encounters(_tables(), "Day 1 00:00")
        self.assertIn(
            "### Регион\n\n≈ 1.5 на зону.\n\n| Отряд | Фракция | Состав | Доля |\n|---|---|---|---|\n"
            "| big | Фракция | Мечник ×1; Охранник 3–5; Главный 2 | 60% |\n"
            "| cond ¹ | Фракция | Беженец 1–9 | 20% |\n"
            "| pick | Фракция | случайные 1–2 из 3 вариантов | 17% |\n"
            "| Прочие (2) | | | 3% |\n\n", text)
        self.assertIn("| Город | Ф1 | big (Мечник ×1; Охранник 3–5; Главный 2)<br>cond ¹ (Беженец 1–9) |\n", text)
        self.assertIn("| Ф1 | -5 | О0 -100 | О1 +0, О2 +50 |\n", text)  # только 3 отношения: фракция достигает порога списка
        self.assertTrue(text.endswith("|\n") and "\r" not in text)

    def test_input_order_does_not_change_output(self) -> None:
        """Ловит зависимость файла от порядка строк таблиц (детерминизм побайтно)."""
        base = _tables()
        shuffled = {name: random.Random(7).sample(rows, len(rows)) for name, rows in base.items()}
        self.assertEqual(build_encounters(base, "Day 1 00:00"), build_encounters(shuffled, "Day 1 00:00"))

    def test_region_encounters_combat_stats_and_leader(self) -> None:
        """Ловит ошибки выделения лидера, расчета min/med/max рядовых, игнорирования заглушек v0=v1=0 и оружия."""
        tables = _showcase_tables()
        rows = {r["squad_sid"]: r for r in build_region_encounters(tables)}
        self.assertIn("bandits", rows)
        b = rows["bandits"]
        self.assertEqual(b["region_name_en"], "Border Zone")
        self.assertEqual(b["source"], "biome_homeless")
        self.assertEqual(b["town_name"], "")
        self.assertEqual(b["squad_name"], "bandits")
        self.assertEqual(b["faction_sid"], "fac_bandits")
        self.assertEqual(b["faction_name"], "Бандиты")
        self.assertEqual(b["spawn_share"], "60%")
        self.assertEqual(b["squad_size_min"], 5)  # 1 leader + 2 b1 + 1 b2 + 1 b3
        self.assertEqual(b["squad_size_max"], 8)  # 1 leader + 4 b1 + 2 b2 + 1 b3
        self.assertEqual(b["members_summary"], "Главарь ×1; Разбойник 2–4; Головорез 1–2; Снайпер 1")
        self.assertEqual(b["leader_template_name"], "Главарь")
        self.assertEqual(b["leader_combat_skill"], 45)
        self.assertEqual(b["leader_weapon"], "Конеруб (Скелеты-кузнецы)")
        self.assertEqual(b["leader_armour"], "Кожанка торговца (Стандартный)")
        # Рядовые: b1(10), b2(20), b3(50); заглушка u_stub (99) отброшена
        self.assertEqual(b["members_combat_min"], 10)
        self.assertEqual(b["members_combat_median"], 20)
        self.assertEqual(b["members_combat_max"], 50)
        self.assertEqual(b["weapons_typical"], "Арбалет; Палка; Тесак")
        self.assertEqual(b["armour_grade_max"], 3)  # max of leader(2), b1(1), b2(1), b3(3); stub(5) отброшен
        self.assertEqual(b["world_state_conditions"], "ws_dust_king")

    def test_region_encounters_edge_cases(self) -> None:
        """Ловит краевые случаи: отряд из одного лидера, отряд животных без лидера, пустоты при отсутствии данных."""
        tables = _showcase_tables()
        rows = {r["squad_sid"]: r for r in build_region_encounters(tables)}

        # Отряд только из лидера: combat_stats рядовых наследуют стат лидера
        boss = rows["boss_solo"]
        self.assertEqual(boss["leader_template_name"], "Босс")
        self.assertEqual(boss["leader_combat_skill"], 75)
        self.assertEqual(boss["leader_weapon"], "Мейто-катана")
        self.assertEqual(boss["leader_armour"], "Латный доспех (Специалист)")
        self.assertEqual(boss["members_combat_min"], 75)
        self.assertEqual(boss["members_combat_median"], 75)
        self.assertEqual(boss["members_combat_max"], 75)
        self.assertEqual(boss["weapons_typical"], "")
        self.assertEqual(boss["armour_grade_max"], 4)
        self.assertEqual(boss["squad_size_min"], 1)
        self.assertEqual(boss["squad_size_max"], 1)

        # Отряд животных: лидера нет, сила берется из animal_strength
        dogs = rows["dogs"]
        self.assertEqual(dogs["leader_template_name"], "")
        self.assertEqual(dogs["leader_combat_skill"], "")
        self.assertEqual(dogs["leader_weapon"], "")
        self.assertEqual(dogs["leader_armour"], "")
        self.assertEqual(dogs["members_combat_min"], 35)
        self.assertEqual(dogs["members_combat_median"], 35)
        self.assertEqual(dogs["members_combat_max"], 35)
        self.assertEqual(dogs["weapons_typical"], "_dog type")
        self.assertEqual(dogs["armour_grade_max"], "")
        self.assertEqual(dogs["squad_size_min"], 3)
        self.assertEqual(dogs["squad_size_max"], 6)

    def test_region_encounters_choosefrom_and_town_roaming(self) -> None:
        """Ловит обработку случайных участников choosefrom list и бродячих отрядов городов town_roaming."""
        tables = _showcase_tables()
        rows = {r["squad_sid"]: r for r in build_region_encounters(tables)}

        guards = rows["guards"]
        self.assertEqual(guards["source"], "town_roaming")
        self.assertEqual(guards["town_name"], "The Hub")
        self.assertEqual(guards["region_name_en"], "The Hub")
        self.assertEqual(guards["spawn_share"], "100%")
        self.assertEqual(guards["faction_sid"], "f_guards")
        self.assertEqual(guards["faction_name"], "Стража")
        self.assertEqual(guards["squad_size_min"], 2)
        self.assertEqual(guards["squad_size_max"], 4)
        self.assertEqual(guards["members_combat_min"], 30)
        self.assertEqual(guards["members_combat_median"], 32)  # median(30, 35) = 32.5 -> round half even 32
        self.assertEqual(guards["members_combat_max"], 35)

    def test_region_encounters_determinism_and_sorting(self) -> None:
        """Ловит нарушение порядка сортировки витрины и недетерминированность вывода."""
        base = _showcase_tables()
        shuffled = {name: random.Random(42).sample(rows, len(rows)) for name, rows in base.items()}
        res1 = build_region_encounters(base)
        res2 = build_region_encounters(shuffled)
        self.assertEqual(res1, res2)

        # Проверка порядка сортировки: region_name_en, source, squad_name, squad_sid
        sort_keys = [(r["region_name_en"], r["source"], r["squad_name"], r["squad_sid"]) for r in res1]
        self.assertEqual(sort_keys, sorted(sort_keys))

    def test_region_encounters_table_wrapper_and_missing_tables(self) -> None:
        """Проверяет создание объекта Table и ошибку при отсутствии обязательных таблиц."""
        tables = _showcase_tables()
        tbl = region_encounters_table(tables)
        self.assertEqual(tbl.name, "region_encounters.csv")
        self.assertEqual(len(tbl.rows), 4)
        self.assertEqual(len(tbl.columns), 23)
        self.assertEqual(tbl.join_keys, "region_name_en, squad_sid, faction_sid")

        bad_tables = {k: v for k, v in tables.items() if k != "unit_templates"}
        with self.assertRaises(ValueError) as ctx:
            build_region_encounters(bad_tables)
        self.assertIn("unit_templates", str(ctx.exception))


class DeliverEncountersTests(unittest.TestCase):
    def git(self, *args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout

    def test_encounters_in_same_commit_idempotent_and_skipped_without_tables(self) -> None:
        """Ловит файл вне коммита выгрузки, лишний коммит при повторе и перезапись файла выпуском без таблиц."""
        with tempfile.TemporaryDirectory() as holder:
            tmp = Path(holder)
            clone, pub = tmp / "clone", tmp / "published"
            clone.mkdir()
            self.git("init", "-b", "main", cwd=clone)
            self.git("config", "user.name", "t", cwd=clone)
            self.git("config", "user.email", "t@example.invalid", cwd=clone)
            (clone / "README.md").write_text("x\n", encoding="utf-8")
            self.git("add", ".", cwd=clone)
            self.git("commit", "-m", "init", cwd=clone)
            (pub / "tables").mkdir(parents=True)
            manifest = {"validation": {"status": "passed"}, "game_time": "Day 1 00:00"}
            (pub / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            for name in REQUIRED_TABLES:
                rows = _tables()[name]
                with (pub / "tables" / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(rows)

            first = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((first["encounters"], first["committed"]), ("written", True))
            self.assertIn("docs/reference/encounters.md", self.git("show", "--name-only", "--format=", "HEAD", cwd=clone))
            self.assertIn("published/manifest.json", self.git("show", "--name-only", "--format=", "HEAD", cwd=clone))
            second = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((second["encounters"], second["committed"]), ("unchanged", False))

            (pub / "tables" / "squad_spawns.csv").unlink()
            before = (clone / "docs/reference/encounters.md").read_bytes()
            third = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual(third["encounters"], "skipped")
            self.assertEqual((clone / "docs/reference/encounters.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
