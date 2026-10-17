"""Collect isolated market emotion snapshots; no formal strategy writes."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading.research.market_emotion.service import (  # noqa: E402
    DEFAULT_OUTPUT,
    TZ,
    build_snapshot,
    collection_lock,
    format_summary,
    save_snapshot,
    themes,
)


def fetch_source(name: str, day: date, raw_dir: Path, python: Path,
                 *, worker_name: str | None = None, board: str = "", symbol: str = "") -> dict:
    path = raw_dir / (name + ".json")
    command = [str(python), str(ROOT / "scripts" / "_fetch_market_emotion_source.py"),
               "--name", worker_name or name, "--date", day.strftime("%Y%m%d"),
               "--output", str(path), "--board", board, "--symbol", symbol]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=35,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if completed.returncode or not path.exists():
            result = {"status": "ERROR", "error": "PROVIDER_WORKER_FAILED"}
        else:
            result = json.loads(path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        result = {"status": "TIMEOUT", "error": "PROVIDER_35_SECOND_DEADLINE"}
    result.update(name=name, raw_path=str(path))
    result.setdefault("available_at", datetime.now(TZ).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result


def collect(day: date, output: Path, python: Path) -> dict:
    if day != datetime.now(TZ).date():
        raise ValueError("Live quotes/concepts cannot be used to reconstruct another date")
    with collection_lock(output):
        raw_dir = output / "raw" / day.strftime("%Y%m%d") / uuid.uuid4().hex
        calendar = fetch_source("ak_calendar", day, raw_dir, python)
        if calendar.get("status") != "OK":
            raise RuntimeError("Trading calendar unavailable: no collection without date validation")
        days = sorted({str(r["trade_date"])[:10] for r in calendar["payload"]["rows"]})
        if day.isoformat() not in days:
            return {"status": "SKIPPED_NON_TRADING_DAY", "trade_date": day.isoformat()}
        earlier = [d for d in days if d < day.isoformat()]
        previous_day = date.fromisoformat(earlier[-1]) if earlier else None
        jobs = ["ak_concepts", "ak_industries", "ak_concept_flow", "ak_limit_up", "ak_limit_down",
                "ak_broken", "tencent_quotes", "zz_sentiment", "zz_hot", "zz_lhb", "ak_lhb"]
        sources = {"ak_calendar": calendar}

        def run(name):
            return name, fetch_source(name, day, raw_dir, python)

        with ThreadPoolExecutor(max_workers=3) as pool:
            sources.update(dict(pool.map(run, jobs)))
        if previous_day:
            sources["ak_previous_limit_up"] = fetch_source(
                "ak_previous_limit_up", day, raw_dir, python, worker_name="ak_previous_pool")
            sources["ak_previous_limit_up"]["reference_trade_date"] = previous_day.isoformat()
        top = themes(sources, day, datetime.now(TZ))["top"][:3]

        def member(board):
            name = "members_" + board["board_id"]
            return name, fetch_source(name, day, raw_dir, python,
                                      worker_name="ak_concept_members", board=board["board_id"])

        with ThreadPoolExecutor(max_workers=3) as pool:
            sources.update(dict(pool.map(member, top)))
        lhb = sources.get("ak_lhb", {})
        if lhb.get("status") == "OK":
            # Bounded sample, not a claim of whole-market seat coverage.
            codes = list(dict.fromkeys(str(r["代码"]).zfill(6)
                                      for r in lhb["payload"]["rows"]))[:3]

            def seat(task):
                side, code = task
                name = "seat_" + side + "_" + code
                return name, fetch_source(name, day, raw_dir, python,
                                          worker_name="ak_lhb_" + side, symbol=code)

            with ThreadPoolExecutor(max_workers=3) as pool:
                sources.update(dict(pool.map(seat, [(side, code) for code in codes
                                                     for side in ["buy", "sell"]])))
        payload = build_snapshot(sources, day, previous_day, datetime.now(TZ))
        return save_snapshot(output, payload)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(TZ).date())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--worker-python", type=Path,
                        default=ROOT / ".runtime" / "market_sentiment_env" / "Scripts" / "python.exe")
    args = parser.parse_args()
    try:
        payload = collect(args.date, args.output.resolve(), args.worker_python.resolve())
        print(format_summary(payload) if "market" in payload else json.dumps(payload, ensure_ascii=False))
        return 0 if payload["status"] in {"CORE_COMPLETE", "SKIPPED_NON_TRADING_DAY"} else 2
    except (ValueError, RuntimeError, OSError) as exc:
        print("Market emotion collection unavailable: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
