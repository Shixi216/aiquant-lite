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
from trading.research.sentiment.black_swan_rules import (
    FORCE_NEGATIVE,
    match_black_swan,
)

TZ = timezone(timedelta(hours=8))
DB = "database/hermes_opc.duckdb"


def load_bars(con, symbol: str):
    # canonical_historical_bars 同一交易日可能同时存在 RAW 与前复权
    # （FORWARD_ADJUSTED）两套行；混读会扭曲均线（SMA60 曾因此偏高）。
    # 每个交易日只取一行：优先前复权，其次按生成时间取最新。
    rows = con.execute(
        """
        SELECT event_time, open, high, low, close, volume
        FROM canonical_historical_bars
        WHERE symbol LIKE ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY trade_date
            ORDER BY
                CASE WHEN adjustment_type = 'FORWARD_ADJUSTED' THEN 0 ELSE 1 END,
                generated_at DESC,
                bar_id
        ) = 1
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
    """合并三张财务报表（利润表/资产负债表/现金流量表）为一个dict

    同时按报告期聚合出 _periods 序列（新→旧），供净利同比等跨期计算。
    """
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
    periods: dict[str, dict] = {}
    for source, payload in rows:
        d = json.loads(payload) if isinstance(payload, str) else payload
        data = d.get("data", d)
        period = str(data.get("report_period") or "")
        bucket = periods.setdefault(period, {}) if period else None
        for key in (
            "total_assets", "total_liab", "n_income_attr_p", "revenue",
            "n_cashflow_act", "basic_eps", "report_period", "oper_cost",
            "invest_income", "dt_netprofit",
            "roe", "roe_dt", "or_yoy", "netprofit_yoy",
        ):
            if key in data and data[key] is not None:
                if key not in merged:
                    merged[key] = data[key]
                if bucket is not None and key not in bucket:
                    bucket[key] = data[key]
    if periods:
        merged["_periods"] = [
            {"period": p, **periods[p]}
            for p in sorted(periods.keys(), reverse=True)
        ]
    real_keys = set(merged.keys()) - {"_periods"}
    return merged if real_keys else None


def technical_factor(bars, market_regime: str | None = None):
    sig = technical_signal(bars, market_regime)
    return {
        "score": sig.score, "confidence": sig.confidence,
        "summary": sig.summary, "evidence": sig.evidence, "risks": sig.risks,
        "market_regime": market_regime,
    }


def fundamental_factor(fin_data):
    """从财务记录构建基本面信号；无数据则中性

    额外返回展示用真实数字：roe/debt_ratio/cashflow/net_profit_yoy/report_period。
    """
    extra = {
        "roe": None, "roe_dt": None, "roe_source": None,
        "revenue_yoy": None, "debt_ratio": None, "cashflow": None,
        "net_profit_yoy": None, "report_period": None,
        "net_income": None,
    }
    if not fin_data:
        return {
            "score": 0.0, "confidence": 0.1,
            "summary": "无财务数据", "risks": ["财务数据缺失"],
            **extra,
        }
    # 从入库记录提取关键指标（视具体存储格式调整）
    try:
        import json
        d = json.loads(fin_data) if isinstance(fin_data, str) else fin_data
        total_assets = d.get("total_assets")
        total_liab = d.get("total_liab")
        net_income = d.get("n_income_attr_p")
        cashflow = d.get("n_cashflow_act")
        # 净利同比：最新报告期净利润 vs 上年同期（report_period 年份-1）
        yoy = None
        periods = d.get("_periods") or []
        if periods:
            cur = periods[0]
            cur_np = cur.get("n_income_attr_p")
            cur_period = str(cur.get("period") or "")
            if cur_np is not None and len(cur_period) == 8 and cur_period.isdigit():
                prev_period = str(int(cur_period[:4]) - 1) + cur_period[4:]
                prev = next(
                    (p for p in periods[1:] if str(p.get("period")) == prev_period),
                    None,
                )
                prev_np = prev.get("n_income_attr_p") if prev else None
                if prev_np not in (None, 0):
                    yoy = (float(cur_np) - float(prev_np)) / abs(float(prev_np))
        official_roe = d.get("roe")
        official_roe_dt = d.get("roe_dt")
        official_or_yoy = d.get("or_yoy")
        official_netprofit_yoy = d.get("netprofit_yoy")
        extra = {
            "roe": None,
            "roe_dt": (
                float(official_roe_dt) / 100 if official_roe_dt is not None else None
            ),
            "roe_source": (
                "Tushare官方加权口径" if official_roe is not None else None
            ),
            "revenue_yoy": (
                float(official_or_yoy) / 100 if official_or_yoy is not None else None
            ),
            "debt_ratio": None, "cashflow": cashflow,
            "net_profit_yoy": (
                float(official_netprofit_yoy) / 100
                if official_netprofit_yoy is not None
                else yoy
            ),
            "report_period": d.get("report_period"),
            "net_income": net_income,
        }
        if not all([total_assets, total_liab, net_income]):
            return {
                "score": 0.0, "confidence": 0.2,
                "summary": "财务字段不全", "risks": [],
                **extra,
            }
        net_asset = total_assets - total_liab
        # 扣非净利：优先 Tushare dt_netprofit，缺失时用归母-投资收益近似
        # （盈新发展 000620 案例：归母8610万全由投资收益3.88亿撑起）
        invest_income = d.get("invest_income")
        deducted = d.get("dt_netprofit")
        if deducted is None and invest_income is not None:
            deducted = net_income - invest_income
        invest_ratio = None
        if invest_income is not None and net_income and net_income > 0:
            invest_ratio = invest_income / net_income
        self_roe = net_income / net_asset if net_asset else None
        roe = float(official_roe) / 100 if official_roe is not None else self_roe
        snapshot = FundamentalSnapshot(
            as_of=datetime.now(tz=TZ).date(),
            roe=roe,
            roe_dt=extra["roe_dt"],
            roe_source=(
                "Tushare官方加权口径"
                if official_roe is not None
                else "自算（官方缺失）"
            ),
            revenue_growth=extra["revenue_yoy"],
            net_profit_growth=extra["net_profit_yoy"],
            debt_ratio=total_liab / total_assets,
            operating_cash_flow_positive=(
                cashflow > 0 if cashflow is not None else None
            ),
            pe_ttm=None,
            net_profit_deducted=deducted,
            invest_income=invest_income,
            invest_income_ratio=invest_ratio,
            evidence_refs=["财报"],
        )
        sig = fundamental_signal(snapshot)
        return {
            "score": sig.score, "confidence": sig.confidence,
            "summary": sig.summary, "risks": sig.risks,
            "roe": snapshot.roe, "debt_ratio": snapshot.debt_ratio,
            "roe_dt": snapshot.roe_dt, "roe_source": snapshot.roe_source,
            "revenue_yoy": snapshot.revenue_growth,
            "cashflow": cashflow, "net_profit_yoy": extra["net_profit_yoy"],
            "report_period": d.get("report_period"),
            "net_profit_deducted": deducted,
            "invest_income": invest_income,
            "invest_income_ratio": invest_ratio,
            "net_income": net_income,
        }
    except Exception as exc:
        return {
            "score": 0.0, "confidence": 0.1,
            "summary": f"财务解析失败: {exc}", "risks": [],
            **extra,
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
        text = title + " " + content
        forced = match_black_swan(
            text,
            source_level=item.get("source_level", "media"),
            source_name=item.get("publisher", ""),
            verified=bool(item.get("verified", False)),
        )
        # 2026-08-26 扩充词表：原表漏"下滑/裁员/劝退/解聘/亏损/减持"等高频
        # 负面词（星宇股份教训：10条负面舆情全部漏报，甚至"净利下滑"误判正面）
        if any(k in text for k in [
            "涨停", "上涨", "抢购", "涨价", "利好", "供应", "预增", "增长",
            "中标", "签约", "回购", "增持", "分红", "扭亏", "盈利", "创新高",
        ]):
            direction = 1
        if any(k in text for k in [
            "跌停", "下跌", "下滑", "下降", "风险", "澄清", "无实质", "不足",
            "异常波动", "砸", "处罚", "调查", "立案", "诉讼", "亏损", "减持",
            "裁员", "劝退", "解聘", "辞退", "违规", "违约", "商誉", "计提",
            "退市", "警示", "问询", "暴跌", "跳水", "承压", "利空",
        ]):
            direction = -1
        if forced is not None:
            direction = -1
        freshness = 1.0
        if pub:
            age_hours = (cutoff - pub).total_seconds() / 3600
            freshness = 0.5 ** (age_hours / 72) if age_hours > 0 else 1.0
        source_w = 1.0 if (
            item.get("source_level") == "official"
            or "证券时报" in item.get("publisher", "")
            or "上海证券报" in item.get("publisher", "")
        ) else 0.5
        intensity = forced.intensity if forced is not None else 0.5
        if forced is not None and pub:
            age_hours = max(0.0, (cutoff - pub).total_seconds() / 3600)
            freshness = 0.5 ** (age_hours / (180 * 24))
        events.append({
            "title": title[:40], "direction": direction,
            "intensity": intensity,
            "score": direction * intensity * freshness * source_w,
            "event_type": forced.event_type.value if forced else "KEYWORD_RULE",
            "force_negative": forced is not None,
            "marker": FORCE_NEGATIVE if forced is not None else "",
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
                source_url=item.get("url", ""),
                source_level=item.get("source_level", "media"),
                verified=bool(item.get("verified", False)),
                content_hash=item.get("content_hash", "h"),
                payload=payload,
            )
            return EventBundle(
                event_cluster_id=f"evt_{abs(hash(title)) % 10**8}",
                canonical_title=title,
                cluster_event_type=(
                    "announcement"
                    if item.get("source_level") == "official"
                    else "UNKNOWN"
                ),
                event_time=pub, data_cutoff=cutoff, primary_source_id=src.record_id,
                source_count=1, source_records=[src], symbols=[symbol],
                sectors=[], dedup_method="single", dedup_version="v1",
                cluster_hash=item.get("content_hash", "ch"),
            )

        async def run():
            extractor = SentimentEventExtractor()
            events = []
            model_used = None
            extract_failures = 0
            total_tried = 0
            for item in news_items[:6]:  # 最多6条，平衡耗时与覆盖
                bundle = make_bundle(item)
                total_tried += 1
                try:
                    result = await extractor.extract(bundle, allow_model_calls=True)
                    ext = result.extraction
                    if getattr(result, "provider", None):
                        model_used = getattr(result, "provider", None)
                    forced_result = any(
                        getattr(flag, "value", str(flag)) == FORCE_NEGATIVE
                        for flag in result.risk_flags
                    )
                    if ext and (
                        result.validation_status.name in ("VALID", "REPAIRED")
                        or forced_result
                    ):
                        # AI 方向/强度 + 新鲜度 + 来源权重
                        pub = bundle.event_time
                        age_hours = (cutoff - pub).total_seconds() / 3600
                        freshness = 0.5 ** (age_hours / 72) if age_hours > 0 else 1.0
                        publisher = item.get("publisher", "")
                        source_w = 1.0 if (
                            item.get("source_level") == "official"
                            or "证券时报" in publisher
                            or "上海证券报" in publisher
                        ) else 0.5
                        score = ext.direction * ext.intensity * freshness * source_w
                        forced = forced_result
                        if forced:
                            freshness = 0.5 ** (max(age_hours, 0.0) / (180 * 24))
                            score = ext.direction * ext.intensity * freshness * source_w
                        events.append({
                            "title": item.get("title", "")[:40],
                            "direction": ext.direction,
                            "intensity": ext.intensity,
                            "score": score,
                            "event_type": ext.event_type.value,
                            "force_negative": forced,
                            "marker": FORCE_NEGATIVE if forced else "",
                        })
                    else:
                        # AI 调用成功但提取无效（如模型不可用返回空）
                        extract_failures += 1
                except Exception:
                    # 2026-08-26 修复：不再静默吞异常，累计失败数
                    extract_failures += 1
                    continue
            if not events and total_tried > 0 and extract_failures == total_tried:
                # 2026-08-26 修复：AI 全部失败（API 403/402/超时等）→
                # 回退关键词规则并标注降级，禁止静默返回 0 分漏报负面舆情
                fallback = sentiment_factor(news_items, cutoff)
                fallback["degraded_reason"] = "情绪AI不可用（API失败），回退关键词规则"
                fallback["model"] = None
                return fallback
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


def _is_penalty_boilerplate(title: str) -> bool:
    """再融资（定增/向特定对象发行）申报的法定披露文件：
    “最近五年被监管措施或处罚及整改情况”属格式性自查，不是新处罚事实。
    盈新发展 000620 2026-08-18 曾因此被误扣分。
    """
    markers = (
        "被监管措施或处罚及整改",
        "采取监管措施或处罚及整改",
        "被证券监管部门和交易所采取监管措施或处罚",
    )
    return any(marker in title for marker in markers)


def policy_factor(announcements):
    event_scores: list[float] = []
    notes = []
    force_negative_events = []
    for a in announcements:
        t = a.get("title", "")
        content = a.get("content", "")
        forced = match_black_swan(
            f"{t} {content}",
            source_level=a.get("source_level", "official"),
            source_name=a.get("publisher", "巨潮资讯"),
            verified=bool(a.get("verified", True)),
        )
        event_score = 0.0
        if forced is not None:
            event_score = -1.0
            event = {
                "date": a.get("date", ""),
                "title": t,
                "event_type": forced.event_type.value,
                "direction": -1,
                "intensity": forced.intensity,
                "marker": FORCE_NEGATIVE,
            }
            force_negative_events.append(event)
            notes.append(
                f"{a.get('date','')} {FORCE_NEGATIVE} {forced.event_type.value}"
                f"（{forced.keyword}）→ -1.0"
            )
        # 利好关键词
        elif any(k in t for k in ["业绩预告", "预增", "回购", "中标", "合同签订", "获得批准",
                                  "取得证书", "增持", "分红", "派息", "扭亏", "盈利", "增长",
                                  "战略合作", "扩产", "投产", "涨价"]):
            event_score = 0.3
            notes.append(f"{a.get('date','')} 利好公告 → +0.3")
        # 利空关键词（真实处罚/风险才扣分）
        boilerplate = _is_penalty_boilerplate(t)
        if forced is None and boilerplate:
            notes.append(f"{a.get('date','')} 再融资配套自查文件（近五年监管措施/处罚及整改）→ 0")
        elif forced is None and any(k in t for k in ["异常波动", "风险提示", "减持", "冻结", "立案", "处罚",
                                  "调查", "诉讼", "亏损", "违规", "警示", "问询", "警示函",
                                  "终止", "退市", "ST"]):
            event_score = min(event_score, -0.2)
            notes.append(f"{a.get('date','')} 风险公告 → -0.2")
        # 中性但有信息量的
        if any(k in t for k in ["延期", "补充流动资金", "归还", "质押", "解禁",
                                  "股东会", "董事会决议", "调整", "变更", "收购", "重组"]):
            notes.append(f"{a.get('date','')} 中性事项 → 0")
        event_scores.append(event_score)
    mean_score = sum(event_scores) / len(event_scores) if event_scores else 0.0
    score = mean_score
    if force_negative_events:
        score = min(mean_score, min(event_scores) * 0.8)
    return {
        "score": max(-1.0, min(1.0, score)),
        "confidence": 0.5,
        "notes": notes,
        "total_announcements": len(announcements),
        "force_negative_events": force_negative_events,
        "mean_score": mean_score,
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
        SELECT payload_json, event_time, source_name, source_level, verified
        FROM data_records
        WHERE symbol LIKE ? AND data_type='announcement'
        ORDER BY event_time DESC
        """,
        [f"%{symbol}%"],
    ).fetchall()
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(days=days)
    items = []
    deduped: dict[str, dict] = {}
    for payload, stored_event_time, source_name, source_level, verified in rows:
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
            "time": stored_event_time,
            "content": d.get("content") or inner.get("content", ""),
            "url": d.get("url") or inner.get("url", ""),
            "publisher": source_name or "巨潮资讯",
            "source_level": str(source_level or "official"),
            "verified": bool(verified),
            "disclosure_source": (
                d.get("disclosure_source")
                or inner.get("disclosure_source")
                or "CNInfo"
            ),
        })
    # 同一公告可能因历史口径修正留下两条记录；报告取更早披露时间。
    for item in items:
        key = "".join(ch for ch in item["title"] if ch.isalnum())
        current = deduped.get(key)
        if current is None or (
            item.get("time") is not None
            and current.get("time") is not None
            and item["time"] < current["time"]
        ):
            deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda item: item.get("time") or datetime.min.replace(tzinfo=TZ),
        reverse=True,
    )


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

    from trading.research.technical.market_regime import get_market_regime
    regime = get_market_regime()
    tech = technical_factor(bars, regime)
    print(f"\n【1. 技术面】得分 {tech['score']:.4f} | 置信 {tech['confidence']:.3f}"
          + (f" | 市况 {regime}" if regime else ""))
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
