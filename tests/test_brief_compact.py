"""brief.md выводит счётчики изменений; построчные дельты остаются в changes.md."""
from __future__ import annotations

import unittest

from tools.changes_render import render_changes_md
from tools.rendering import render_brief


CHANGES = {
    "valid": True,
    "from": {"day": 1, "time": "10:00", "snapshot_id": "a"},
    "to": {"day": 1, "time": "11:00", "snapshot_id": "b"},
    "world": {"money": {"from": 100, "to": 150, "delta": 50}},
    "skills": [{"id": f"ps:{i}", "name": f"Боец {i}", "changes": {"Athletics": {"from": 10.01, "to": 10.02}}}
               for i in range(20)],
    "transfers": [{"item": "Хлеб", "from": {"A": 1}, "to": {"B": 1}}],
    "diplomacy": [{"faction_name": "Болотники", "relation": {"from": -2.0, "to": -2.5, "delta": -0.5}}],
    "world_state": {"unique_npcs": [{"name_ru": "Тень", "state_code": {"from": 1, "to": 2}}]},
}
CURRENT = {"world": {"day": 1, "time": "11:00", "money": 150},
           "characters": [{"id": "ps:1", "squad_id": "s"}], "squads": [{"squad_id": "s", "squad_name": "A"}]}


class BriefCompactTests(unittest.TestCase):
    def test_brief_counts_and_changes_md_lists(self) -> None:
        brief = render_brief(CURRENT, CHANGES, "b", locations={"spatial_groups": []})
        self.assertIn("**Изменения навыков:** у 20 участников", brief)
        self.assertNotIn("Athletics: 10.01", brief)
        self.assertIn("**Перемещения предметов:** 1", brief)
        self.assertIn("**Изменения отношений с фракциями:** 1", brief)
        self.assertIn("Лидер `Тень`", brief)
        self.assertIn("**Казна:** 100 -> 150 (+50)", brief)
        self.assertLess(len(brief.encode("utf-8")), 3072)

        changes_md = render_changes_md(CHANGES, [])
        self.assertIn("Athletics: 10.01 -> 10.02", changes_md)
        self.assertIn("`Болотники`", changes_md)


if __name__ == "__main__":
    unittest.main()
