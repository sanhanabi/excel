from __future__ import annotations

import argparse
from pathlib import Path

from excel_assistant.planners.llama_cpp import LlamaCppPlanner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start the bundled llama.cpp runtime, wait for health, then stop it."
    )
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--startup-timeout", type=int, default=180)
    args = parser.parse_args()

    planner = None
    try:
        planner = LlamaCppPlanner(
            model_path=str(args.model),
            server_path=str(args.server),
            startup_timeout_seconds=args.startup_timeout,
        )
        print("Standalone runtime health check: OK")
    finally:
        if planner is not None:
            planner.close()


if __name__ == "__main__":
    main()
