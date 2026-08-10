from __future__ import annotations

import argparse
from datetime import date

from trading.review.daily import build_review_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a safe structured daily trading review.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Review date (YYYY-MM-DD)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    review_date = date.fromisoformat(args.date)
    print(build_review_service().daily_review_markdown(review_date))


if __name__ == "__main__":
    main()
