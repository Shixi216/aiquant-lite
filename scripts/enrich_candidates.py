from scripts.full_market_cli import main_for


def main() -> int:
    return main_for("enrich_candidates")


if __name__ == "__main__":
    raise SystemExit(main())
