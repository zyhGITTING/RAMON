from __future__ import annotations

import argparse
import json
from typing import Any

from backend.app.services.sync_service import run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Datamid datasource sync")
    parser.add_argument("--source-key", default="", help="Sync a single datasource by source_key")
    parser.add_argument("--triggered-by", default="system", help="Trigger source label")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result: dict[str, Any] = run_sync(
        source_key=args.source_key or None,
        triggered_by=args.triggered_by,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
