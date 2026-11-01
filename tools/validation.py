"""Проверка проектных схем Draft 2020-12; полной схемы legacy-сейва нет."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from tools.io_utils import read_json

ROOT = Path(__file__).resolve().parents[1]


def validate(value: Any, schema_name: str) -> None:
    if schema_name not in {"manifest", "timeline-event"}:
        raise ValueError("Неизвестная проектная схема")
    schema = read_json((ROOT / "contracts" / f"{schema_name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def check_examples() -> int:
    count = 0
    for path in sorted((ROOT / "examples").glob("*.json")):
        value = read_json(path.read_bytes())
        validate(value, "manifest" if value.get("format") == "kenshi-publication/1" else "timeline-event")
        count += 1
    return count
