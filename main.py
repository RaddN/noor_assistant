from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def run_check() -> int:
    from standalone_assistant.core.connectors import ToolRegistry
    from standalone_assistant.core.project_scanner import codex_status
    from standalone_assistant.core.storage import Storage

    storage = Storage()
    storage.initialize()
    registry = ToolRegistry(storage)
    tools = registry.list_tools()
    payload = {
        "database": str(storage.db_path),
        "tools": [
            {
                "id": tool["id"],
                "name": tool["name"],
                "path": tool["path"],
                "exists": Path(tool["path"]).exists(),
                "enabled": bool(tool["enabled"]),
            }
            for tool in tools
        ],
        "codex": codex_status(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ESEO Standalone Windows Assistant")
    parser.add_argument("--check", action="store_true", help="Initialize local data and print connector status.")
    args = parser.parse_args()

    if args.check:
        return run_check()

    from standalone_assistant.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
