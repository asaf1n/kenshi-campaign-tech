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
from tools.encounters import REQUIRED_TABLES, build_encounters


def _spawn(place: str, squad: str, v0: str, source: str = "biome_homeless", faction: str = "") -> dict[str, str]:
    return {"source": source, "place_sid": place, "place_name_en": place, "place_name_ru": place, "place_faction_sid": faction,
            "spawn_amount": "1.5" if source == "biome_homeless" else "", "squad_sid": squad, "squad_name": squad, "ref_v0": v0}


def _member(squad: str, role: str, name: str, v0: str, v1: str = "0") -> dict[str, str]:
    return {"squad_sid": squad, "squad_name": squad, "role": role, "member_sid": name, "member_name": name,
            "member_type": "CHARACTER", "v0": v0, "v1": v1, "v2": "0"}


def _template(sid: str, faction: str = "Фракция", states: str = "", chars: tuple[str, str] = ("0", "0")) -> dict[str, str]:
    return {"sid": sid, "faction_names": faction, "world_state_sids": states,
            "num_random_chars": chars[0], "num_random_chars_max": chars[1]}


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
