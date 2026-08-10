"""个股五维研究命令 — 用法: python scripts/analyze_stock.py 002407 [报告日期]

五维：技术面 / 基本面 / 情绪面 / 政策新闻面 / 资金面
数据源：Tushare Pro / 腾讯 / BaoStock / 巨潮（本地 DuckDB 缓存）
正式决策：技术面60% + 基本面40%，风险 VETO 由决策层执行。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta

import duckdb

from trading.schemas import Bar, FundamentalSnapshot
from trading.research.technical.analysis import technical_signal
from trading.research.fundamental.analysis import fundamental_signal

TZ = timezone(timedelta(hours=8))
DB = "database/hermes_opc.duckdb"


def load_bars(con, symbol: str):
    rows = con.execute(
        """
        SELECT event_time, open, high, low, close, volume
        FROM canonical_historical_bars
        WHERE symbol LIKE ?
        ORDER BY event_time
        """,
        [f"%{symbol}%"],
    ).fetchall()
    return [
        Bar(
            trade_date=r[0].date(),
            open=float(r[1]), high=float(r[2]),
            low=float(r[3]), close=float(r[4]), volume=float(r[5]),
        )
        for r in rows
    ]


def load_financials(con, symbol: str):
    """合并三张财务报表（利润表/资产负债表/现金流量表）为一个dict"""
    import json
    rows = con.execute(
        """
        SELECT source_name, payload_json FROM data_records
        WHERE symbol LIKE ? AND data_type='financial_statement'
        ORDER BY event_time DESC
        """,
        [f"%{symbol}%"],
    ).fetchall()
    if not rows:
        return None
    merged: dict = {}
    for source, payload in rows:
        d = json.loads(payload) if isinstance(payload, str) else payload
        data = d.get("data", d)
        for key in (
            "total_assets", "total_liab", "n_income_attr_p", "revenue",
            "n_cashflow_act", "basic_eps", "report_period", "oper_cost",
        ):
            if key in data and data[key] is not None and key not in merged:
                merged[key] = data[key]
    return merged if merged else None


def technical_factor(bars):
    sig = technical_signal(bars)
    return {
        "score": sig.score, "confidence": sig.confidence,
        "summary": sig.summary, "risks": sig.risks,
    }


def fundamental_factor(fin_data):
    """从财务记录构建基本面信号；无数据则中性"""
    if not fin_data:
        return {
            "score": 0.0, "confidence": 0.1,
            "summary": "无财务数据", "risks": ["财务数据缺失"],
            "roe": None, "debt_ratio": None,
        }
    # 从入库记录提取关键指标（视具体存储格式调整）
    try:
        import json
        d = json.loads(fin_data) if isinstance(fin_data, str) else fin_data
        total_assets = d.get("total_assets")
        total_liab = d.get("total_liab")
        net_income = d.get("n_income_attr_p")
        if not all([total_assets, total_liab, net_income]):
            return {
                "score": 0.0, "confidence": 0.2,
                "summary": "财务字段不全", "risks": [],
                "roe": None, "debt_ratio": None,
            }
        net_asset = total_assets - total_liab
        cashflow = d.get("n_cashflow_act")
        snapshot = FundamentalSnapshot(
            as_of=datetime.now(tz=TZ).date(),
            roe=net_income / net_asset if net_asset else None,
            revenue_growth=None,
            debt_ratio=total_liab / total_assets,
            operating_cash_flow_positive=(
                cashflow > 0 if cashflow is not None else None
            ),
            pe_ttm=None,
            evidence_refs=["财报"],
        )
        sig = fundamental_signal(snapshot)
        return {
            "score": sig.score, "confidence": sig.confidence,
            "summary": sig.summary, "risks": sig.risks,
            "roe": snapshot.roe, "debt_ratio": snapshot.debt_ratio,
        }
    except Exception as exc:
        return {
            "score": 0.0, "confidence": 0.1,
            "summary": f"财务解析失败: {exc}", "risks": [],
            "roe": None, "debt_ratio": None,
        }


def capital_flow_factor(bars):
    if len(bars) < 2:
        return {"score": 0.0, "confidence": 0.5, "pv_label": "数据不足"}
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
    return {"score": pv_score, "confidence": 0.75, "pv_label": pv_label}


def sentiment_factor(news_items, cutoff: datetime):
    events = []
    for item in news_items:
        title = item.get("title", "")
        content = item.get("content", "")
        pub = item.get("time")
        direction = 0
        if any(k in title + content for k in ["涨停", "上涨", "抢购", "涨价", "利好", "供应", "预增", "增长"]):
            direction = 1
        if any(k in title + content for k in ["跌停", "下跌", "风险", "澄清", "无实质", "不足", "异常波动", "砸", "处罚", "调查"]):
            direction = -1
        freshness = 1.0
        if pub:
            age_hours = (cutoff - pub).total_seconds() / 3600
            freshness = 0.5 ** (age_hours / 72) if age_hours > 0 else 1.0
        source_w = 1.0 if "证券时报" in item.get("publisher", "") or "上海证券报" in item.get("publisher", "") else 0.5
        events.append({
            "title": title[:40], "direction": direction,
            "score": direction * 0.5 * freshness * source_w,
        })
    n = len(events)
    score = sum(e["score"] for e in events) / (n ** 0.5) if n else 0.0
    return {
        "score": max(-1.0, min(1.0, score)),
        "confidence": 0.4,
        "events": events,
    }


def sentiment_factor_ai(symbol: str, news_items: list, cutoff: datetime) -> dict:
    """qwen AI 情绪面增强：用模型提取事件方向/强度，替代关键词规则。

    返回与 sentiment_factor 相同结构；AI 不可用时自动回退关键词规则。
    """
    if not news_items:
        return {"score": 0.0, "confidence": 0.4, "events": [], "model": None}

    try:
        import asyncio
        from trading.research.sentiment.models import EventBundle, EventSourceRecord
        from trading.research.sentiment.event_extractor import SentimentEventExtractor

        def make_bundle(item) -> EventBundle:
            title = item.get("title", "") or ""
            content = item.get("content", "") or ""
            pub = item.get("time") or datetime.now(tz=TZ)
            if isinstance(pub, str):
                try:
                    pub = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except ValueError:
                    pub = datetime.now(tz=TZ)
            payload = {"title": title, "content": content, "publisher": item.get("publisher", "")}
            src = EventSourceRecord(
                record_id=item.get("record_id", f"news_{abs(hash(title)) % 10**8}"),
                event_time=pub, fetched_at=datetime.now(tz=TZ),
                source_name=item.get("publisher", "media"),
                source_url=item.get("url", ""), source_level="media",
                verified=False, content_hash=item.get("content_hash", "h"),
                payload=payload,
            )
            return EventBundle(
                event_cluster_id=f"evt_{abs(hash(title)) % 10**8}",
                canonical_title=title, cluster_event_type="UNKNOWN",
                event_time=pub, data_cutoff=cutoff, primary_source_id=src.record_id,
                source_count=1, source_records=[src], symbols=[symbol],
                sectors=[], dedup_method="single", dedup_version="v1",
                cluster_hash=item.get("content_hash", "ch"),
            )

        async def run():
            extractor = SentimentEventExtractor()
            events = []
            model_used = None
            for item in news_items[:6]:  # 最多6条，平衡耗时与覆盖
                bundle = make_bundle(item)
                try:
                    result = await extractor.extract(bundle, allow_model_calls=True)
                    ext = result.extraction
                    if getattr(result, "provider", None):
                        model_used = getattr(result, "provider", None)
                    if ext and result.validation_status.name in ("VALID", "REPAIRED"):
                        # AI 方向/强度 + 新鲜度 + 来源权重
                        pub = bundle.event_time
                        age_hours = (cutoff - pub).total_seconds() / 3600
                        freshness = 0.5 ** (age_hours / 72) if age_hours > 0 else 1.0
                        publisher = item.get("publisher", "")
                        source_w = 1.0 if "证券时报" in publisher or "上海证券报" in publisher else 0.5
                        score = ext.direction * ext.intensity * freshness * source_w
                        events.append({
                            "title": item.get("title", "")[:40],
                            "direction": ext.direction,
                            "intensity": ext.intensity,
                            "score": score,
                            "event_type": ext.event_type.value,
                        })
                except Exception:
                    continue
            n = len(events)
            score = sum(e["score"] for e in events) / (n ** 0.5) if n else 0.0
            return {
                "score": max(-1.0, min(1.0, score)),
                "confidence": 0.6 if model_used else 0.4,
                "events": events,
                "model": model_used,
            }

        try:
            return asyncio.run(run())
        except RuntimeError:
            # 事件循环冲突（已在循环中）→ 回退关键词
            return sentiment_factor(news_items, cutoff)
    except Exception:
        # AI 不可用 → 回退关键词规则
        return sentiment_factor(news_items, cutoff)


def policy_factor(announcements):
    score = 0.0
    notes = []
    for a in announcements:
        t = a.get("title", "")
        # 利好关键词
        if any(k in t for k in ["业绩预告", "预增", "回购", "中标", "合同签订", "获得批准",
                                  "取得证书", "增持", "分红", "派息", "扭亏", "盈利", "增长",
                                  "战略合作", "扩产", "投产", "涨价"]):
            score += 0.3
            notes.append(f"{a.get('date','')} 利好公告 → +0.3")
        # 利空关键词
        if any(k in t for k in ["异常波动", "风险提示", "减持", "冻结", "立案", "处罚",
                                  "调查", "诉讼", "亏损", "违规", "警示", "问询", "警示函",
                                  "终止", "退市", "ST"]):
            score -= 0.2
            notes.append(f"{a.get('date','')} 风险公告 → -0.2")
        # 中性但有信息量的
        if any(k in t for k in ["延期", "补充流动资金", "归还", "质押", "解禁",
                                  "股东会", "董事会决议", "调整", "变更", "收购", "重组"]):
            score += 0.0  # 中性，仅记录
            notes.append(f"{a.get('date','')} 中性事项 → 0")
    return {
        "score": max(-1.0, min(1.0, score)),
        "confidence": 0.5,
        "notes": notes,
        "total_announcements": len(announcements),
    }


def load_news(con, symbol: str):
    """从 data_records 读取新闻（兼容嵌套 data 结构）"""
    import json
    rows = con.execute(
        """
        SELECT payload_json FROM data_records
        WHERE symbol LIKE ? AND data_type='finance_news'
        ORDER BY event_time DESC
        """,
        [f"%{symbol}%"],
    ).fetchall()
    items = []
    for (payload,) in rows:
        d = json.loads(payload) if isinstance(payload, str) else payload
        # 兼容嵌套结构：payload 可能是 {..., "data": {...}}
        inner = d.get("data") if isinstance(d.get("data"), dict) else d
        raw_time = (d.get("time") or d.get("published_at")
                    or inner.get("time") or inner.get("published_at") or "")
        try:
            t = datetime.fromisoformat(str(raw_time)).replace(tzinfo=TZ) if raw_time else None
        except Exception:
            t = None
        items.append({
            "title": d.get("title") or inner.get("title", ""),
            "content": d.get("content") or inner.get("content", ""),
            "publisher": d.get("publisher") or inner.get("publisher", ""),
            "url": d.get("url") or inner.get("url", ""),
            "time": t,
        })
    return items


def load_announcements(con, symbol: str, days: int = 30):
    """从 data_records 读取公告（默认近30天）"""
    import json
    from datetime import datetime, timezone, timedelta
    rows = con.execute(
        """
        SELECT payload_json FROM data_records
        WHERE symbol LIKE ? AND data_type='announcement'
        ORDER BY event_time DESC
        """,
        [f"%{symbol}%"],
    ).fetchall()
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(days=days)
    items = []
    for (payload,) in rows:
        d = json.loads(payload) if isinstance(payload, str) else payload
        inner = d.get("data") if isinstance(d.get("data"), dict) else d
        raw_date = str(inner.get("date") or inner.get("announcement_date")
                       or d.get("date") or d.get("announcement_date") or "")
        # 时间过滤：YYYYMMDD 格式
        try:
            if len(raw_date) == 8 and raw_date.isdigit():
                dtime = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone(timedelta(hours=8)))
            else:
                dtime = None
            if dtime and dtime < cutoff:
                continue
        except ValueError:
            pass
        if len(raw_date) == 8 and raw_date.isdigit():
            fmt_date = raw_date[4:6] + "-" + raw_date[6:8]
        elif len(raw_date) >= 10:
            fmt_date = raw_date[5:7] + "-" + raw_date[8:10]
        else:
            fmt_date = raw_date
        items.append({
            "title": d.get("title") or inner.get("title", ""),
            "date": fmt_date,
        })
    return items


def main():
    parser = argparse.ArgumentParser(description="个股五维研究")
    parser.add_argument("symbol", help="股票代码，如 002407")
    parser.add_argument("--date", default=None, help="数据截止日期 YYYY-MM-DD，默认今天")
    args = parser.parse_args()

    symbol = args.symbol.strip()
    cutoff = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=TZ)
        if args.date else datetime.now(tz=TZ)
    )

    con = duckdb.connect(DB, read_only=True)
    bars = load_bars(con, symbol)
    if not bars:
        print(f"错误：数据库中没有 {symbol} 的行情数据。")
        print("请先通过 finance_data MCP 拉取数据（get_daily_bars 等）再运行本命令。")
        sys.exit(1)

    print("=" * 56)
    print(f"{symbol} 五维研究报告 | 数据截止 {cutoff.date()}")
    print("=" * 56)
    print(f"【行情】K线 {len(bars)} 根 | 最新收盘 {bars[-1].close:.2f} | 日期 {bars[-1].trade_date}")

    tech = technical_factor(bars)
    print(f"\n【1. 技术面】得分 {tech['score']:.4f} | 置信 {tech['confidence']:.3f}")
    print(f"   {tech['summary']}")
    if tech["risks"]:
        print(f"   风险: {tech['risks']}")

    fin = load_financials(con, symbol)
    fund = fundamental_factor(fin)
    print(f"\n【2. 基本面】得分 {fund['score']:.4f} | 置信 {fund['confidence']:.3f}")
    print(f"   {fund['summary']}")
    if fund["risks"]:
        print(f"   风险: {fund['risks']}")

    cap = capital_flow_factor(bars)
    print(f"\n【3. 资金面】得分 {cap['score']:.4f} | {cap['pv_label']}")

    news_items = load_news(con, symbol)
    senti = sentiment_factor(news_items, cutoff)
    print(f"\n【4. 情绪面】得分 {senti['score']:.4f} | 置信 {senti['confidence']:.3f}")
    for e in senti["events"]:
        print(f"   {e['direction']:>+2} | {e['title']}")

    anns = load_announcements(con, symbol)
    policy = policy_factor(anns)
    print(f"\n【5. 政策/公告面】得分 {policy['score']:.4f} | 置信 {policy['confidence']:.3f}")
    for n in policy["notes"]:
        print(f"   {n}")

    combined = tech["score"] * 0.6 + fund["score"] * 0.4
    conf = tech["confidence"] * 0.6 + fund["confidence"] * 0.4
    stance = "看多(≥0.2)" if combined >= 0.2 else "看空(≤-0.2)" if combined <= -0.2 else "中性"
    print(f"\n{'=' * 56}")
    print(f"【正式决策 60/40】综合分 {combined:.4f} | 置信 {conf:.3f} | 立场: {stance}")
    print(f"{'=' * 56}")
    print("\n⚠️ 本报告仅为系统规则计算的信息整理，不构成投资建议。")


if __name__ == "__main__":
    main()
