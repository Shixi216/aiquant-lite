"""资金面增强：龙虎榜 + 两融数据（替代仅量价代理）

数据源（Tushare，2146积分可用）：
- margin_detail：融资融券明细（融资余额/买入/融券余量）
- top_list：龙虎榜（机构/游资净买卖）

评分逻辑（本地确定性规则）：
- 两融趋势：融资余额变化方向（增=看多，减=看空）
- 融资买入强度：近期买入 vs 前期
- 龙虎榜信号：净买入/净卖出、机构席位
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)


def get_tushare_pro():
    """获取 Tushare Pro 客户端（读 .env token）"""
    import tushare as ts
    from config.settings import settings
    if not settings.tushare_token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    ts.set_token(settings.tushare_token.strip())
    return ts.pro_api()


def fetch_margin(symbol: str, days: int = 10) -> list[dict]:
    """拉取两融明细（近 N 个交易日）"""
    try:
        pro = get_tushare_pro()
        end = datetime.now(tz=TZ).date()
        start = end - timedelta(days=days * 1.6)
        ts_code = _ts_code(symbol)
        df = pro.margin_detail(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        return df.sort_values("trade_date").to_dict("records")
    except Exception as exc:  # 2026-09-28 M5修复：不再静默，留排查线索
        logger.warning("fetch_margin(%s) 失败: %s: %s", symbol, type(exc).__name__, exc)
        return []


def fetch_top_list(symbol: str, days: int = 30) -> list[dict]:
    """拉取近期龙虎榜记录（该股上榜日）。

    Tushare top_list 接口只支持按单日 trade_date 查询（不支持 ts_code 范围），
    故先取交易日历，再逐日拉取并筛选目标股票。
    """
    try:
        pro = get_tushare_pro()
        ts_code = _ts_code(symbol)
        end = datetime.now(tz=TZ).date()
        start = end - timedelta(days=days)
        trade_days: list[str] = []
        try:
            cal = pro.trade_cal(
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                is_open="1",
            )
            trade_days = sorted(cal["cal_date"].astype(str).tolist())
        except Exception as exc:  # 2026-09-28 M5修复：不静默
            logger.warning("fetch_top_list(%s) trade_cal 失败: %s", symbol, type(exc).__name__)
            trade_days = []
        records: list[dict] = []
        for td in trade_days:
            try:
                df = pro.top_list(trade_date=td)
                if df is not None and not df.empty:
                    hit = df[df["ts_code"] == ts_code]
                    if not hit.empty:
                        records.extend(hit.to_dict("records"))
            except Exception as exc:  # 2026-09-28 M5修复：单日失败不静默
                logger.warning("fetch_top_list(%s) %s 单日失败: %s", symbol, td, type(exc).__name__)
                continue
        records.sort(key=lambda r: r.get("trade_date", ""))
        return records
    except Exception as exc:  # 2026-09-28 M5修复：不再静默，留排查线索
        logger.warning("fetch_top_list(%s) 失败: %s: %s", symbol, type(exc).__name__, exc)
        return []


def _ts_code(symbol: str) -> str:
    """标准化 Tushare ts_code（防双重后缀 bug，2026-09-07）。

    symbol 可能传入两种格式：纯6位代码（"601058"）或已带后缀
    （"601058.SH" / "000779.SZ"）。旧实现无条件拼后缀，传入
    "601058.SH" 会拼出 "601058.SH.SH"，Tushare 返回空 → 资金面
    四个数据项（换手/两融/龙虎榜/主力净额）静默全缺失。
    """
    symbol = (symbol or "").strip().upper()
    if "." in symbol:  # 已带后缀（.SH/.SZ/.BJ），直接返回
        return symbol
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError(f"非法股票代码: {symbol!r}")
    if symbol.startswith("6") or symbol.startswith("9"):
        return f"{symbol}.SH"
    if symbol.startswith(("0", "2", "3")):
        return f"{symbol}.SZ"
    if symbol.startswith(("4", "8")):
        return f"{symbol}.BJ"
    raise ValueError(f"无法识别交易所后缀的股票代码: {symbol!r}")


def fetch_moneyflow(symbol: str, days: int = 35) -> list[dict]:
    """拉取近 N 个自然日的个股资金流向（Tushare moneyflow，单位万元）"""
    try:
        pro = get_tushare_pro()
        end = datetime.now(tz=TZ).date()
        start = end - timedelta(days=days)
        df = pro.moneyflow(
            ts_code=_ts_code(symbol),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        return df.sort_values("trade_date").to_dict("records")
    except Exception as exc:  # 2026-09-28 M5修复：不再静默，留排查线索
        logger.warning("fetch_moneyflow(%s) 失败: %s: %s", symbol, type(exc).__name__, exc)
        return []


def aggregate_moneyflow(mf_rows: list[dict]) -> dict:
    """主力资金 5/10/20 日累计净额（元）。主力=超大单+大单；不足窗口返回 None。"""
    def day_net(r) -> float:
        buy = float(r.get("buy_elg_amount") or 0) + float(r.get("buy_lg_amount") or 0)
        sell = float(r.get("sell_elg_amount") or 0) + float(r.get("sell_lg_amount") or 0)
        return (buy - sell) * 1e4  # 万元 → 元

    nets = [day_net(r) for r in mf_rows]  # 已按 trade_date 升序
    out = {"data_count": len(nets)}
    for n in (5, 10, 20):
        out[f"net{n}"] = sum(nets[-n:]) if len(nets) >= n else None
    out["net1"] = nets[-1] if nets else None
    out["last_date"] = str(mf_rows[-1].get("trade_date")) if mf_rows else None
    return out


def fetch_turnover(symbol: str, days: int = 45) -> dict | None:
    """拉取近20个交易日换手率和最新流通市值（Tushare daily_basic）。"""
    try:
        pro = get_tushare_pro()
        end = datetime.now(tz=TZ).date()
        start = end - timedelta(days=days)
        df = pro.daily_basic(
            ts_code=_ts_code(symbol),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,turnover_rate,circ_mv",
        )
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date").tail(20)
        rate = df.iloc[-1].get("turnover_rate")
        if rate is None or rate != rate:  # NaN check
            return None
        valid_rates = [float(value) for value in df["turnover_rate"].dropna().tolist()]
        percentile = (
            sum(value <= float(rate) for value in valid_rates) / len(valid_rates)
            if valid_rates
            else None
        )
        circ_mv = df.iloc[-1].get("circ_mv")
        return {
            "turnover_rate": float(rate),
            "trade_date": str(df.iloc[-1].get("trade_date")),
            "turnover_percentile_20d": percentile,
            "sample_count": len(valid_rates),
            "circ_mv": (
                None if circ_mv is None or circ_mv != circ_mv else float(circ_mv)
            ),
        }
    except Exception as exc:  # 2026-09-28 M5修复：不再静默，留排查线索
        logger.warning("fetch_turnover(%s) 失败: %s: %s", symbol, type(exc).__name__, exc)
        return None


def score_margin(margin_rows: list[dict]) -> dict:
    """两融评分：融资余额趋势 + 融资买入强度"""
    if len(margin_rows) < 3:
        return {
            "score": 0.0,
            "confidence": 0.4,
            "label": "两融数据不足",
            "details": [],
            "data_count": len(margin_rows),
        }

    # 融资余额趋势（首日 vs 末日）
    first_bal = float(margin_rows[0].get("rzye") or 0)
    last_bal = float(margin_rows[-1].get("rzye") or 0)
    bal_change = (last_bal - first_bal) / first_bal if first_bal else 0

    # 融资买入趋势（近3日均 vs 前3日均）
    recent_buy = sum(float(r.get("rzmre") or 0) for r in margin_rows[-3:]) / 3
    early_buy = sum(float(r.get("rzmre") or 0) for r in margin_rows[:3]) / 3
    buy_change = (recent_buy - early_buy) / early_buy if early_buy else 0

    # 综合评分：余额变化 60% + 买入趋势 40%
    score = 0.0
    if bal_change > 0.05:
        score += 0.3
    elif bal_change < -0.05:
        score -= 0.3
    if buy_change > 0.2:
        score += 0.2
    elif buy_change < -0.2:
        score -= 0.2

    # 幅度归一化
    score = max(-0.8, min(0.8, score))
    details = [
        f"融资余额: {first_bal/1e8:.2f}亿 → {last_bal/1e8:.2f}亿 ({bal_change*100:+.1f}%)",
        f"融资买入: {early_buy/1e8:.2f}亿/日 → {recent_buy/1e8:.2f}亿/日 ({buy_change*100:+.1f}%)",
    ]
    return {
        "score": score,
        "confidence": min(0.85, 0.5 + 0.05 * len(margin_rows)),
        "label": "两融" + ("净流入" if score > 0.1 else "净流出" if score < -0.1 else "平衡"),
        "details": details,
        "data_count": len(margin_rows),
    }


def score_top_list(top_rows: list[dict]) -> dict:
    """龙虎榜评分：净买入方向 + 强度"""
    if not top_rows:
        return {"score": 0.0, "confidence": 0.3, "label": "未上榜", "details": [],
                "data_count": 0, "total_net": None, "first_date": None, "last_date": None}

    total_net = sum(float(r.get("net_amount") or 0) for r in top_rows)
    total_amount = sum(float(r.get("l_amount") or 0) for r in top_rows)
    net_rate = total_net / total_amount if total_amount else 0

    score = 0.0
    if net_rate > 0.1:
        score += 0.4
    elif net_rate < -0.1:
        score -= 0.4
    else:
        score += net_rate * 2

    # 上榜次数越多越活跃（不直接加分，只记录）
    details = [
        f"上榜{len(top_rows)}次 | 净买入 {total_net/1e8:.2f}亿 | 净占比 {net_rate*100:+.1f}%",
    ]
    for r in top_rows[-3:]:
        details.append(f"  {r['trade_date']} {r.get('reason','')[:20]} 净额{r.get('net_amount',0)/1e8:+.2f}亿")
    return {
        "score": max(-0.8, min(0.8, score)),
        "confidence": min(0.85, 0.4 + 0.1 * len(top_rows)),
        "label": "龙虎榜" + ("净买入" if score > 0.1 else "净卖出" if score < -0.1 else "平衡"),
        "details": details,
        "data_count": len(top_rows),
        "total_net": total_net,
        "first_date": str(top_rows[0].get("trade_date")),
        "last_date": str(top_rows[-1].get("trade_date")),
    }


def capital_flow_factor_enhanced(
    symbol: str,
    bars: list,
    *,
    allow_external_fetch: bool = True,
) -> dict:
    """资金面增强：量价 + 两融 + 龙虎榜

    返回与 capital_flow_factor 兼容结构（score/confidence/pv_label + 增强字段）
    """
    # 1. 最新换手与流通市值；高换手标签优先于简单量价标签。
    turnover = fetch_turnover(symbol) if allow_external_fetch else None
    base = _pv_score(bars, turnover)

    # 2. 两融增强
    margin_rows = fetch_margin(symbol) if allow_external_fetch else []
    margin = score_margin(margin_rows)

    # 3. 龙虎榜增强
    top_rows = fetch_top_list(symbol) if allow_external_fetch else []
    top = score_top_list(top_rows)

    # 4. 主力资金净额（5/10/20日）与换手率（仅展示，不进评分权重）
    if allow_external_fetch:
        moneyflow = aggregate_moneyflow(fetch_moneyflow(symbol))
    else:
        moneyflow = {"data_count": 0, "net5": None, "net10": None, "net20": None, "net1": None, "last_date": None}
    circ_mv = turnover.get("circ_mv") if turnover else None
    net5 = moneyflow.get("net5")
    mf5_ratio = (
        net5 / (circ_mv * 1e4)
        if net5 is not None and circ_mv not in (None, 0)
        else None
    )

    # 合成：量价 40% + 两融 40% + 龙虎榜 20%（有数据时）
    weights, scores, confs = [], [], []
    if base.get("has_data"):
        weights.append(0.4)
        scores.append(base["score"])
        confs.append(base["confidence"])
    if margin.get("data_count", 0) >= 3:
        weights.append(0.4)
        scores.append(margin["score"])
        confs.append(margin["confidence"])
    if top.get("data_count", 0) > 0:
        weights.append(0.2)
        scores.append(top["score"])
        confs.append(top["confidence"])

    if not weights:
        return {"score": 0.0, "confidence": 0.4, "pv_label": "数据不足",
                "details": ["无可用资金数据"],
                "moneyflow": moneyflow, "turnover": turnover,
                "circ_mv": circ_mv, "mf5_ratio": mf5_ratio,
                "top_list": top, "margin": margin}

    total_w = sum(weights)
    combined = sum(s * w for s, w in zip(scores, weights)) / total_w
    confidence = sum(c * w for c, w in zip(confs, weights)) / total_w

    details = [base.get("pv_label", "")]
    details.extend(margin.get("details", []))
    details.extend(top.get("details", []))
    return {
        "score": max(-1.0, min(1.0, combined)),
        "confidence": min(0.9, confidence),
        "pv_label": base.get("pv_label", ""),
        "label": margin.get("label", "") + " / " + top.get("label", ""),
        "details": details,
        "margin": margin,
        "top_list": top,
        "moneyflow": moneyflow,
        "turnover": turnover,
        "circ_mv": circ_mv,
        "mf5_ratio": mf5_ratio,
    }


def _pv_score(bars: list, turnover: dict | None = None) -> dict:
    """量价基础评分（从 analyze_stock 复制，保证兼容）"""
    if not bars or len(bars) < 2:
        return {"score": 0.0, "confidence": 0.5, "pv_label": "数据不足", "has_data": False}
    last, prev = bars[-1], bars[-2]
    price_up = last.close > prev.close
    vol_up = last.volume > prev.volume
    if price_up and vol_up:
        pv_score, pv_label = 0.6, "价涨量增"
    elif price_up and not vol_up:
        pv_score, pv_label = -0.15, "价涨量缩"
    elif not price_up and vol_up:
        pv_score, pv_label = -0.7, "价跌量增"
    else:
        pv_score, pv_label = -0.2, "价跌量缩"
    percentile = turnover.get("turnover_percentile_20d") if turnover else None
    if percentile is not None and percentile >= 0.80:
        pv_label = f"高换手（近20日分位{percentile:.0%}）"
    return {"score": pv_score, "confidence": 0.75, "pv_label": pv_label, "has_data": True}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    symbol = sys.argv[1] if len(sys.argv) > 1 else "601991"
    start = time.perf_counter()
    result = capital_flow_factor_enhanced(symbol, [])
    print(f"=== 资金面增强测试 {symbol} ===")
    print(f"综合得分: {result['score']:+.3f} | 置信度: {result['confidence']:.2f}")
    print(f"标签: {result['label']}")
    for d in result["details"]:
        print(f"  {d}")
    print(f"耗时: {time.perf_counter()-start:.1f}s")
