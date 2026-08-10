from scripts.full_market_cli import main_for


def main() -> int:
    return main_for("generate_data_coverage_report")


if __name__ == "__main__":
    raise SystemExit(main())
