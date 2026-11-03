"""Smoke-check стартового комплекта, не тест игрового экспортёра.

Запуск: python tests/check_seed.py
"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def run() -> None:
    required = [
        'README.md', 'AGENTS.md', 'CLAUDE.md', 'GEMINI.md',
        'prompts/01-START-AGENT.md', 'prompts/02-EXECUTE-APPROVED.md',
        'prompts/03-LLM-INSTRUCTIONS.md', 'orchestrate_workflow/method.md',
        'docs/requirements.md', 'docs/data-contract.md',
        'docs/architecture.md', 'docs/acceptance.md',
        'docs/multiagent.md', 'docs/chatgpt-interface.md',
        'references/observed-export.md', 'references/assessment-nov-2026.md',
        'contracts/manifest.schema.json', 'contracts/timeline-event.schema.json',
        'examples/manifest.example.json', 'examples/event.example.json'
    ]
    missing = [p for p in required if not (ROOT / p).is_file()]
    assert not missing, f'Missing files: {missing}'

    for name in ['manifest', 'timeline-event']:
        obj = json.loads((ROOT / 'contracts' / f'{name}.schema.json').read_text(encoding='utf-8'))
        assert obj['$schema'].endswith('/2020-12/schema')
        assert obj['type'] == 'object'

    manifest = json.loads((ROOT / 'examples/manifest.example.json').read_text(encoding='utf-8'))
    event = json.loads((ROOT / 'examples/event.example.json').read_text(encoding='utf-8'))
    assert manifest['format'] == 'kenshi-publication/1'
    assert re.fullmatch(r'[0-9a-f]{64}', manifest['source_sha256'])
    assert manifest['source_kind'] == 'export'
    assert manifest['source_format'] == 'kenshi-session/10'
    assert set(('current', 'changes', 'brief', 'squad')).issubset(manifest['artifacts'])
    assert event['format'] == 'kenshi-event/1'
    assert event['status'] == 'confirmed'
    assert event['evidence']
    print('Seed files: OK. Real save processing and JSON Schema validation: NOT TESTED.')


if __name__ == '__main__':
    run()
