from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.utf8 import UTF8_ERRORS, write_utf8


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNICODE_REPORT = "中文涨幅 12.5% | 综合分 ≥ 0.3 | 区间 ±0.1 → 建仓 ✅ ⚠️"


class _LegacyGbkStream:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, value: str) -> int:
        value.encode("gbk", errors="strict")
        return len(value)

    def flush(self) -> None:
        return None


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"report": UNICODE_REPORT}


def test_write_utf8_survives_legacy_gbk_stream() -> None:
    stream = _LegacyGbkStream()
    write_utf8(UNICODE_REPORT, stream=stream)
    assert stream.buffer.getvalue().decode("utf-8") == UNICODE_REPORT + "\n"


def test_stock_report_cli_posts_once_when_gbk_cannot_encode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from scripts import stock_report

    calls: list[dict[str, object]] = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return _Response()

    stream = _LegacyGbkStream()
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "argv", ["stock_report.py", "600172", "--no-fetch"])

    stock_report.main()

    assert len(calls) == 1
    output = stream.buffer.getvalue().decode("utf-8")
    assert UNICODE_REPORT in output
    assert "中文" in output
    assert "12.5%" in output
    assert "≥" in output
    assert "±" in output
    assert "→" in output
    assert "✅" in output
    assert "⚠️" in output


def test_child_process_overrides_gbk_stdio_with_utf8() -> None:
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "gbk:strict"
    environment["PYTHONUTF8"] = "0"
    child = (
        "from config.utf8 import configure_utf8_stdio; "
        "configure_utf8_stdio(); "
        f"print({UNICODE_REPORT!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", child],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors=UTF8_ERRORS,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == UNICODE_REPORT


def test_report_declares_every_required_timing_stage() -> None:
    from trading.research.stock_report_service import TIMING_STAGES

    assert TIMING_STAGES == [
        "行情",
        "K线",
        "财务",
        "公告",
        "新闻",
        "情绪AI",
        "数据库",
        "五维计算",
        "DecisionEngine",
        "总耗时",
    ]
