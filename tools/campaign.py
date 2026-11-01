"""CLI этапа извлечения: python -m tools.campaign."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tools.config import Config
from tools.extraction import extract
from tools.io_utils import canonical
from tools.normalization import scrub


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Неизменяемые снимки Kenshi")
    commands = parser.add_subparsers(dest="command", required=True)
    extract_parser = commands.add_parser("extract")
    extract_parser.add_argument("--source-kind", choices=("save", "export"), required=True)
    extract_parser.add_argument("--source", type=Path, required=True)
    extract_parser.add_argument("--output", type=Path)
    extract_parser.add_argument("--faction-sid")
    extract_parser.add_argument("--campaign-id")
    extract_parser.add_argument("--branch-id")
    extract_parser.add_argument("--parent-snapshot-id")
    commands.add_parser("check-schemas")
    args = parser.parse_args(argv)
    try:
        if args.command == "check-schemas":
            from tools.validation import check_examples
            result = {"validated_examples": check_examples()}
        else:
            result = extract(args.source, args.source_kind, Config.from_env(args.faction_sid), args.output,
                             args.campaign_id, args.branch_id, args.parent_snapshot_id)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
