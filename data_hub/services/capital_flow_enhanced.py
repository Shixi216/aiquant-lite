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

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

TZ = timezone(timedelta(hours=8))


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
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        df = pro.margin_detail(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        return df.sort_values("trade_date").to_dict("records")
    except Exception:
        return []


def fetch_top_list(symbol: str, days: int = 10) -> list[dict]:
    """拉取近期龙虎榜记录（该股上榜日）"""
    try:
        pro = get_tushare_pro()
        end = datetime.now(tz=TZ).date()
        start = end - timedelta(days=days * 1.6)
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        df = pro.top_list(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        return df.sort_values("trade_date").to_dict("records")
    except Exception:
        return []


def score_margin(margin_rows: list[dict]) -> dict:
    """两融评分：融资余额趋势 + 融资买入强度"""
    if len(margin_rows) < 3:
        return {"score": 0.0, "confidence": 0.4, "label": "两融数据不足", "details": []}

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
        return {"score": 0.0, "confidence": 0.3, "label": "未上榜", "details": [], "data_count": 0}

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
    }


def capital_flow_factor_enhanced(symbol: str, bars: list) -> dict:
    """资金面增强：量价 + 两融 + 龙虎榜

    返回与 capital_flow_factor 兼容结构（score/confidence/pv_label + 增强字段）
    """
    # 1. 量价基础分（原有逻辑）
    base = _pv_score(bars)

    # 2. 两融增强
    margin_rows = fetch_margin(symbol)
    margin = score_margin(margin_rows)

    # 3. 龙虎榜增强
    top_rows = fetch_top_list(symbol)
    top = score_top_list(top_rows)

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
                "details": ["无可用资金数据"]}

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
    }


def _pv_score(bars: list) -> dict:
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
