"""备用数据源（2026-10-15 新增）。

用途：Tushare 抖动/限频时，为资金面数据提供独立备用源，避免"主源一抖，
资金面就缺数据"。**仅在主源失败/不足时启用**，主源正常不触碰（零额外耗时）。

覆盖（管理员 2026-10-15 审定，只保留实测稳定的两个）：
- 两融明细 → AKShare（东财）stock_margin_detail_sse / stock_margin_detail_szse
- 龙虎榜 → AKShare（东财）stock_lhb_detail_em

**已移除（2026-10-15 管理员决定「只删不稳的」）**：
- 主力资金 东财 ulist：口径虽与 Tushare 一致，但该路径**时通时断**，不稳定 → 删。
  主力资金维持 Tushare 单源；拿不到就如实报"数据不足"。
- 财务只读快照：未接入评分链、价值有限 → 删。财务维持 Tushare 单源。

设计原则：
- 所有函数失败一律返回空，绝不抛异常打穿调用方。
- 返回结构对齐 capital_flow_enhanced.py 的既有消费口径（两融单位元、龙虎榜净额单位元）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))


def _pure_code(symbol: str) -> str:
    return (symbol or "").strip().upper().split(".")[0]


def _is_sz(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if s.endswith(".SZ"):
        return True
    if s.endswith(".SH") or s.endswith(".BJ"):
        return False
    return _pure_code(s)[0] in "023"


# ---------------------------------------------------------------- 1) 两融
def akshare_margin_rows(symbol: str, days: int = 8) -> list[dict]:
    """AKShare（东财）逐日取两融明细，返回 [{trade_date,rzye,rzmre,rzche,rqye}]（单位元，升序）。

    东财接口按"单日全市场"返回，故逐日调用后筛出目标股。作为 Tushare margin_detail 的兜底。
    """
    code = _pure_code(symbol)
    if len(code) != 6 or not code.isdigit():
        return []
    if code[0] in "48":  # 北交所：东财两融接口不覆盖
        return []
    try:
        import akshare as ak
    except Exception:  # noqa: BLE001
        return []

    want_sz = _is_sz(symbol)
    rows: list[dict] = []
    seen: set[str] = set()
    day = datetime.now(tz=TZ).date()
    for _ in range(days * 2 + 6):
        day = day - timedelta(days=1)
        if day.weekday() >= 5:  # 跳过周末
            continue
        ds = day.strftime("%Y%m%d")
        if ds in seen:
            continue
        seen.add(ds)
        try:
            df = ak.stock_margin_detail_szse(date=ds) if want_sz else ak.stock_margin_detail_sse(date=ds)
        except Exception:  # noqa: BLE001
            continue
        if df is None or len(df) == 0:
            continue
        col_code = "证券代码" if "证券代码" in df.columns else None
        if col_code is None:
            continue
        hit = df[df[col_code].astype(str).str.zfill(6) == code]
        if hit.empty:
            continue
        r = hit.iloc[0].to_dict()

        def _f(*keys):
            for k in keys:
                if r.get(k) not in (None, "", "-"):
                    try:
                        return float(r[k])
                    except (TypeError, ValueError):
                        pass
            return None

        rows.append({
            "trade_date": ds,
            "rzye": _f("融资余额"),
            "rzmre": _f("融资买入额"),
            "rzche": _f("融资偿还额", "融资偿还"),
            "rqye": _f("融券余额", "融券余量"),
        })
        if len(rows) >= days:
            break
    rows.sort(key=lambda x: x["trade_date"])
    if rows:
        logger.warning("backup: 两融走 AKShare 东财源（%s，%d 行）", symbol, len(rows))
    return rows


# ---------------------------------------------------------------- 2) 龙虎榜
def akshare_top_list_rows(symbol: str, days: int = 30) -> list[dict]:
    """AKShare（东财）龙虎榜明细，返回 [{trade_date,net_amount,l_amount,reason}]（净额/买入额单位元）。"""
    code = _pure_code(symbol)
    if len(code) != 6 or not code.isdigit():
        return []
    try:
        import akshare as ak
    except Exception:  # noqa: BLE001
        return []
    end = datetime.now(tz=TZ).date()
    start = end - timedelta(days=days)
    try:
        df = ak.stock_lhb_detail_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("backup akshare_top_list_rows(%s) 失败: %s", symbol, type(exc).__name__)
        return []
    if df is None or len(df) == 0 or "代码" not in df.columns:
        return []
    hit = df[df["代码"].astype(str).str.zfill(6) == code]
    rows: list[dict] = []
    for _, r in hit.iterrows():
        def _f(*keys):
            for k in keys:
                if r.get(k) not in (None, "", "-"):
                    try:
                        return float(r[k])
                    except (TypeError, ValueError):
                        pass
            return None
        td = r.get("上榜日")
        rows.append({
            "trade_date": str(td).replace("-", ""),
            "net_amount": _f("龙虎榜净买额"),
            "l_amount": _f("龙虎榜买入额"),
            "reason": str(r.get("上榜原因") or ""),
        })
    rows.sort(key=lambda x: x["trade_date"])
    if rows:
        logger.warning("backup: 龙虎榜走 AKShare 东财源（%s，%d 行）", symbol, len(rows))
    return rows
