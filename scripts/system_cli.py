from __future__ import annotations

import argparse
import json

from router.integration.health import SystemHealthService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Hermes-OPC health and doctor commands."
    )
    parser.add_argument("command", choices=("health", "doctor"))
    args = parser.parse_args()
    service = SystemHealthService()
    result = service.collect() if args.command == "health" else service.doctor()
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
