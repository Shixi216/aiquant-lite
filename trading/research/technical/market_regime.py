"""大盘市况判定服务（RISING/SIDEWAYS/FALLING）。

供技术因子市况分层过热惩罚使用（2026-09-27，overheat A/B 回放实证：
惩罚仅 SIDEWAYS 有效，RISING/FALLING 无效甚至拖累 → RISING 市减半）。

设计要点：
- 数据源：Tushare index_daily 上证指数 000001.SH，20 日窗口判定。
- 缓存：同一交易日结果落盘缓存（.runtime/market_regime_YYYYMMDD.json），
  同一交易日多次分析只拉一次指数，避免重复调用 Tushare。
- 失败降级：任何环节异常都返回 None（= 历史无差别惩罚基线），
  绝不因市况判定失败阻断个股分析。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # hermes-opc 项目根
CACHE_DIR = ROOT / ".runtime"
INDEX_CODE = "000001.SH"  # 上证指数
WINDOW = 20


def _cache_path(day: date) -> Path:
    return CACHE_DIR / f"market_regime_{day.strftime('%Y%m%d')}.json"


def _read_cache(day: date) -> str | None:
    path = _cache_path(day)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        regime = payload.get("regime")
        # 2026-09-29 第二批M4修复：盘中首算的缓存只有截至昨日的EOD数据。
        # 规则：盘中（15:05前）复用缓存是安全的（EOD当日不会变）；
        # 收盘后如果 data_through 仍 < day，说明缓存还是昨日口径，
        # 判过期触发重算，拿到当日K线后（data_through=day）长期有效。
        data_through = payload.get("data_through")
        if data_through is not None:
            try:
                from datetime import datetime, timezone, timedelta
                now_bj = datetime.now(timezone(timedelta(hours=8)))
                market_closed = (
                    now_bj.date() > day
                    or (now_bj.date() == day and now_bj.hour >= 15)
                )
                if market_closed and data_through < day.isoformat():
                    return None  # 收盘后仍是旧口径 → 过期重算
            except Exception:
                pass
        return regime if regime in {"RISING", "SIDEWAYS", "FALLING", "UNKNOWN"} else None
    except Exception:
        return None


def _write_cache(day: date, regime: str, window_ret: float | None,
                 data_through_iso: str | None = None) -> None:
    # 2026-09-29 第二批M4修复：记录数据实际截至日。盘中调用时拉到的
    # EOD 只到前一交易日，须如实标记（data_through=最后一根K线日期），
    # 供 _read_cache 判断盘中缓存是否过期。
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(day).write_text(
            json.dumps(
                {
                    "regime": regime,
                    "index": INDEX_CODE,
                    "window": WINDOW,
                    "window_return": window_ret,
                    "data_through": data_through_iso,
                    "computed_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass  # 缓存失败不影响主流程


def _fetch_index_closes(day: date, lookback_days: int = 60) -> tuple[list[float], str | None]:
    """返回 (closes, 最后一根K线日期YYYYMMDD)。"""
    from data_hub.providers.tushare_provider import TushareProvider

    provider = TushareProvider()
    start = (day - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")
    rows = provider.get_index_daily(INDEX_CODE, start, end)
    dated = sorted(
        ((str(r["trade_date"]), float(r["close"])) for r in rows),
        key=lambda x: x[0],
    )
    closes = [c for _, c in dated]
    last_date = dated[-1][0] if dated else None
    return closes, last_date


def get_market_regime(day: date | None = None, *, use_cache: bool = True) -> str | None:
    """返回当前市况：RISING / SIDEWAYS / FALLING / UNKNOWN；失败返回 None。

    None 表示判定不可用——调用方应按历史无差别惩罚基线处理。
    """
    from trading.research.technical.analysis import _detect_market_regime

    target = day or date.today()
    if use_cache:
        cached = _read_cache(target)
        if cached is not None:
            return cached
    try:
        closes, last_date = _fetch_index_closes(target)
        regime = _detect_market_regime(closes, WINDOW)
        window_ret = (
            closes[-1] / closes[-WINDOW - 1] - 1 if len(closes) > WINDOW else None
        )
        # 2026-09-29 M4：data_through 用真实最后一根K线日期（YYYYMMDD→
        # ISO 便于与 day 比较），盘中调用时它<target，收盘后=target。
        data_through_iso = (
            f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:8]}" if last_date else None
        )
        _write_cache(target, regime, window_ret, data_through_iso=data_through_iso)
        return regime
    except Exception:
        return None  # 降级：判定失败 = 无差别惩罚基线


if __name__ == "__main__":
    # 手动验证入口：python -m trading.research.technical.market_regime
    print("当前市况:", get_market_regime(use_cache=False))
