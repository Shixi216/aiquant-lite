from scripts.full_market_cli import main_for


def main() -> int:
    return main_for("sync_market_snapshot")


if __name__ == "__main__":
    raise SystemExit(main())
