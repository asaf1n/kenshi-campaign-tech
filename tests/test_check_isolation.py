"""Сквозные проверки на реальных сейвах не пишут в рабочие data/ (индекс, снимки, дельты, локации)."""

from __future__ import annotations

from pathlib import Path
import re
import unittest

TESTS = Path(__file__).resolve().parent
WORKING_DATA = re.compile(r'ROOT\s*/\s*"data/(?!cache\b)')


class CheckScriptIsolationTest(unittest.TestCase):
    def test_check_scripts_use_temporary_data(self) -> None:
        offenders = [
            f"{path.name}:{number}"
            for path in sorted(TESTS.glob("check_*.py"))
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if WORKING_DATA.search(line)
        ]
        self.assertEqual(offenders, [], "Сквозная проверка обращается к рабочим data/")


if __name__ == "__main__":
    unittest.main()
