from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx


ROUTER_URL = "http://127.0.0.1:8765"
SYMBOLS = ("000001.SZ", "000002.SZ", "600519.SH")
LOCK_MARKERS = ("could not set lock", "conflicting lock", "database is locked")


def _request(
    method: str,
    path: str,
    *,
    profile: str = "default",
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 180,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = httpx.request(
            method,
            f"{ROUTER_URL}{path}",
            json=payload,
            params=params,
            headers={"X-Hermes-Profile": profile},
            timeout=timeout,
            trust_env=False,
        )
        text = response.text
        return {
            "ok": response.is_success,
            "status_code": response.status_code,
            "elapsed": time.perf_counter() - started,
            "lock_error": any(marker in text.casefold() for marker in LOCK_MARKERS),
            "body": text[:500],
        }
    except Exception as exc:
        text = str(exc)
        return {
            "ok": False,
            "status_code": None,
            "elapsed": time.perf_counter() - started,
            "lock_error": any(marker in text.casefold() for marker in LOCK_MARKERS),
            "body": f"{type(exc).__name__}: {text}"[:500],
        }


def _research(symbol: str, profile: str = "default") -> dict[str, Any]:
    return _request(
        "POST",
        "/v1/research/stock-report",
        profile=profile,
        payload={
            "symbol": symbol,
            "no_fetch": True,
            "no_ai": True,
            "stale_ok": True,
        },
    )


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = sorted(float(item["elapsed"]) for item in results)
    if not elapsed:
        return {"count": 0}
    p95_index = min(len(elapsed) - 1, math.ceil(len(elapsed) * 0.95) - 1)
    return {
        "count": len(results),
        "completed": sum(bool(item["ok"]) for item in results),
        "failed": sum(not bool(item["ok"]) for item in results),
        "average_seconds": statistics.fmean(elapsed),
        "p50_seconds": statistics.median(elapsed),
        "p95_seconds": elapsed[p95_index],
        "maximum_seconds": max(elapsed),
        "database_lock_errors": sum(bool(item["lock_error"]) for item in results),
        "status_codes": sorted({item["status_code"] for item in results if item["status_code"]}),
    }


def profile_scenario() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    profiles = ("default", "user1", "user2")

    def worker(profile: str, symbol: str) -> list[dict[str, Any]]:
        return [_research(symbol, profile) for _ in range(10)]

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(worker, profile, SYMBOLS[index])
            for index, profile in enumerate(profiles)
        ]
        for future in as_completed(futures):
            results.extend(future.result())
    return _summary(results)


def three_stock_scenario() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for _ in range(10):
            futures = [pool.submit(_research, symbol) for symbol in SYMBOLS]
            results.extend(future.result() for future in as_completed(futures))
    return _summary(results)


def sequential_scenario() -> dict[str, Any]:
    return _summary([_research(SYMBOLS[index % 3]) for index in range(30)])


def mixed_scenario(duration_seconds: int) -> dict[str, Any]:
    stop = threading.Event()
    results: dict[str, list[dict[str, Any]]] = {
        "scanner": [],
        "refresh": [],
        "research": [],
        "profiles": [],
        "shadow_status": [],
    }

    def loop(name: str, action: Callable[[], dict[str, Any]], pause: float) -> None:
        while not stop.is_set():
            results[name].append(action())
            stop.wait(pause)

    actions = [
        (
            "scanner",
            lambda: _request(
                "POST",
                "/v1/scanner/scan",
                payload={"query": "筛选A股前20只", "top_n": 20, "persist_run": False},
            ),
            10.0,
        ),
        (
            "refresh",
            lambda: _request(
                "GET",
                f"/v1/stocks/{SYMBOLS[0]}/realtime-quote",
                params={"persist": True},
            ),
            60.0,
        ),
        ("research", lambda: _research(SYMBOLS[0]), 1.0),
        ("research", lambda: _research(SYMBOLS[1]), 1.0),
        ("research", lambda: _research(SYMBOLS[2]), 1.0),
        (
            "profiles",
            lambda: _request("GET", f"/v1/universe/{SYMBOLS[0]}", profile="user1"),
            2.0,
        ),
        (
            "profiles",
            lambda: _request("GET", f"/v1/universe/{SYMBOLS[1]}", profile="user2"),
            2.0,
        ),
        (
            "shadow_status",
            lambda: _request("GET", "/v1/research/overheat-shadow/status"),
            5.0,
        ),
    ]
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        futures = [pool.submit(loop, *item) for item in actions]
        time.sleep(duration_seconds)
        stop.set()
        for future in futures:
            future.result(timeout=240)
    flattened = [item for values in results.values() for item in values]
    return {
        "duration_seconds": duration_seconds,
        "overall": _summary(flattened),
        "components": {name: _summary(values) for name, values in results.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mixed-seconds", type=int, default=600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "started_at": datetime.now().astimezone().isoformat(),
        "profiles": profile_scenario(),
        "three_stocks_parallel_10_rounds": three_stock_scenario(),
        "thirty_stocks_sequential": sequential_scenario(),
        "mixed": mixed_scenario(args.mixed_seconds),
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
