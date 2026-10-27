"""全市场横截面四因子 rank 预计算（盘后，供决策链读取）

在快照刷新完成后运行，计算全市场 4 因子的横截面 rank + 四维加权分，
写入 cross_section_factor_snapshot 表。决策链 DECISION 阶段直接读目标股票的 rank，
无需实时算全市场。

因子（与 Strategy Lab 回测严格一致）：
  tech   = technical_score 的 pandas pct rank
  fund   = fundamental_score 的 pandas pct rank
  sent   = 情绪面 4 特征 duckdb rank 合成（龙虎榜机构/净额、换手、缺口跟随）
  margin = 两融净买入 的 pandas pct rank
  composite = 30/30/25/15 available 归一化加权

运行（离线维护身份）：
  HERMES_DB_MAINTENANCE=1 python -m scripts.compute_cross_section_snapshot
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))


def _open_db(read_only: bool = True):
    from database.db import open_database
    return open_database(str(Path("database/hermes_opc.duckdb")), read_only=read_only)


def load_latest_snapshot(con) -> dict:
    """读最新快照 run + items，返回换手/OHLC/两融/龙虎榜 dict。"""
    run = con.execute(
        "SELECT snapshot_id, data_cutoff FROM market_snapshot_runs "
        "ORDER BY snapshot_time DESC LIMIT 1"
    ).fetchone()
    if run is None:
        raise RuntimeError("无可用快照")
    snapshot_id, data_cutoff = run[0], run[1]
    rows = con.execute(
        "SELECT symbol, open, high, low, price, previous_close, turnover_rate, payload_json "
        "FROM market_snapshot_items WHERE snapshot_id=?",
        [snapshot_id],
    ).fetchall()
    turnover: dict[str, float] = {}
    gap: dict[str, float] = {}
    margin: dict[str, float] = {}
    inst: dict[str, float] = {}
    dt: dict[str, float] = {}
    for symbol, o, h, low_value, c, pc, tr, payload in rows:
        del h, low_value
        s = str(symbol)
        if tr is not None:
            turnover[s] = float(tr)
        payload = json.loads(payload) if payload else {}
        if c is not None and o is not None and pc is not None and o != 0 and pc != 0:
            open_gap = float(o) / float(pc) - 1.0
            intraday = float(c) / float(o) - 1.0
            direction = 0 if open_gap == 0 else (1 if open_gap > 0 else -1)
            gap[s] = max(0.0, direction * intraday)
        if payload.get("margin_net_buy") is not None:
            margin[s] = float(payload["margin_net_buy"])
        if payload.get("dragon_tiger_net_amount") is not None:
            dt[s] = float(payload["dragon_tiger_net_amount"])
        if payload.get("dragon_tiger_institution_net_amount") is not None:
            inst[s] = float(payload["dragon_tiger_institution_net_amount"])
    return {
        "snapshot_id": snapshot_id, "data_cutoff": data_cutoff,
        "turnover": turnover, "gap": gap, "margin": margin, "inst": inst, "dt": dt,
    }


def compute_technical_scores(con) -> dict[str, float]:
    """全市场技术分（复用 dataset_builder 的向量化复刻，与 technical_signal 一致）。

    市况分层（2026-09-28）：与生产 technical_signal 对齐——RISING 市过热惩罚减半。
    快照为全市场单一时点截面，市况循环外判定一次即可。
    """
    from trading.research.technical.market_regime import get_market_regime
    from trading.research.strategy_lab.dataset_builder import _technical_group
    regime = get_market_regime()
    bars = con.execute(
        "SELECT symbol, trade_date, high, close, volume FROM canonical_historical_bars "
        "WHERE close IS NOT NULL ORDER BY symbol, trade_date"
    ).fetchdf()
    frames = []
    for _, g in bars.groupby("symbol", sort=False, observed=True):
        frames.append(_technical_group(g.reset_index(drop=True), regime))
    tech = pd.concat(frames, ignore_index=True)
    # 取每只股票最近一天的技术分
    tech = tech.dropna(subset=["technical_score"])
    tech = tech.sort_values("feature_trade_date").groupby("symbol", sort=False).tail(1)
    return dict(zip(tech["symbol"], tech["technical_score"]))


def compute_fundamental_scores(con, cutoff) -> dict[str, float]:
    """全市场基本分（复用生产 fundamental 计算）。"""
    from trading.research.fundamental.analysis import fundamental_signal
    from trading.research.fundamental.metrics import calculate_fundamental_metrics
    from trading.research.fundamental.models import MarketValuationPoint
    from trading.research.fundamental.repository import FundamentalRepository
    from trading.schemas import FundamentalSnapshot

    records = con.execute(
        "SELECT canonical_record_id, symbol, data_type, report_period, "
        "announcement_time, data_available_time, event_time, primary_source, "
        "verification_status, source_record_ids_json, confidence, statement_type, "
        "statement_version, revision_of_record_id, accounting_scope, period_type, "
        "source_type, disclosure_time_source, payload_json "
        "FROM canonical_financial_records "
        "WHERE data_available_time IS NOT NULL AND verification_status <> 'CONFLICT' "
        "ORDER BY symbol, data_available_time, event_time, canonical_record_id"
    ).fetchall()
    grouped: dict[str, list] = {}
    for row in records:
        grouped.setdefault(str(row[1]), []).append(
            FundamentalRepository._financial_record(row)
        )
    # 只需要每只股票最新一条收盘价。此前把全部历史价格（当前约 279 万行）
    # 拉入 Pandas 后再 groupby，会与技术分计算争抢内存并在盘后刷新时失败。
    # 在 DuckDB 内完成分组，结果规模降到股票数量，评分语义保持不变。
    latest_prices = con.execute(
        "SELECT symbol, arg_max(close, trade_date) AS close "
        "FROM canonical_historical_bars WHERE close IS NOT NULL GROUP BY symbol"
    ).fetchall()
    price_map = {str(symbol): close for symbol, close in latest_prices}

    out: dict[str, float] = {}
    for symbol, recs in grouped.items():
        close = price_map.get(symbol)
        valuation = None
        if close is not None and np.isfinite(float(close)) and close > 0:
            valuation = MarketValuationPoint(
                canonical_record_id=f"cs:{symbol}", source_record_ids=[],
                event_time=cutoff, close=float(close),
            )
        metrics, missing, flags, selected = calculate_fundamental_metrics(
            records=recs, valuation_point=valuation
        )
        snapshot = FundamentalSnapshot(
            as_of=cutoff.date(), pe_ttm=metrics.pe_ttm, pb=metrics.pb, roe=metrics.roe,
            revenue_growth=metrics.revenue_growth, net_profit_growth=metrics.net_profit_growth,
            debt_ratio=metrics.debt_ratio, operating_cash_flow=metrics.operating_cash_flow,
            operating_cash_flow_positive=metrics.operating_cash_flow_positive, peg=metrics.peg,
            net_profit_deducted=metrics.net_profit_deducted,
            invest_income=metrics.invest_income,
            invest_income_ratio=metrics.invest_income_ratio,
            report_period=metrics.report_period, statement_types=metrics.statement_types,
            data_available_time=max(
                (r.data_available_time for r in selected if r.data_available_time is not None),
                default=None,
            ),
            primary_source=selected[0].primary_source if selected else None,
            verification_status="MIXED" if selected else None,
            source_type="CANONICAL" if selected else None,
            evidence_refs=[r.canonical_record_id for r in selected],
        )
        sig = fundamental_signal(snapshot)
        if selected and sig.confidence > 0.2:
            out[symbol] = sig.score
    return out


def main() -> None:
    import os
    # 声明写主库身份（与 refresh_snapshot_standalone 一致），
    # 否则读/写主库会被 DatabaseOwnershipError 拦截。
    os.environ["HERMES_DB_OWNER_PROCESS"] = "router"
    con = _open_db(read_only=True)
    try:
        snap = load_latest_snapshot(con)
        print(f"快照: {snap['snapshot_id']}  cutoff={snap['data_cutoff']}")
        print(f"换手 {len(snap['turnover'])} / 缺口 {len(snap['gap'])} / 两融 {len(snap['margin'])} / 龙虎榜净额 {len(snap['dt'])} / 机构净额 {len(snap['inst'])}")
        tech = compute_technical_scores(con)
        print(f"技术分: {len(tech)} 只")
        fund = compute_fundamental_scores(con, snap["data_cutoff"])
        print(f"基本分: {len(fund)} 只")
    finally:
        con.close()

    from trading.research.orchestration.cross_section import (
        pandas_pct_rank, sentiment_composite, weighted_composite,
    )
    sent = sentiment_composite(snap["turnover"], snap["gap"], snap["inst"], snap["dt"])
    tech_r = pandas_pct_rank(tech)
    fund_r = pandas_pct_rank(fund)
    sent_r = pandas_pct_rank(sent)
    margin_r = pandas_pct_rank(snap["margin"])
    composite = weighted_composite({"tech": tech_r, "fund": fund_r, "sent": sent_r, "margin": margin_r})

    # 写表：主表仅保留最新截面（消费方 schema 不变），追加历史，落分位数阈值
    con = _open_db(read_only=False)
    try:
        # 幂等建表（与 migration v0123 一致）——Router 未重启时脚本仍可独立运行
        con.execute(
            """CREATE TABLE IF NOT EXISTS cross_section_thresholds (
                data_cutoff TIMESTAMPTZ NOT NULL, p50 DOUBLE NOT NULL, p75 DOUBLE NOT NULL,
                p90 DOUBLE NOT NULL, p95 DOUBLE NOT NULL, strong_buy_q DOUBLE NOT NULL,
                buy_q DOUBLE NOT NULL, n_total BIGINT NOT NULL,
                generated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (data_cutoff))"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS cross_section_factor_snapshot_history (
                snapshot_id VARCHAR NOT NULL, data_cutoff TIMESTAMPTZ NOT NULL,
                symbol VARCHAR NOT NULL, tech_rank DOUBLE, fund_rank DOUBLE,
                sent_rank DOUBLE, margin_rank DOUBLE, composite_score DOUBLE,
                generated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (snapshot_id, symbol))"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS cross_section_factor_snapshot(
                snapshot_id VARCHAR, symbol VARCHAR, data_cutoff TIMESTAMP WITH TIME ZONE,
                tech_rank DOUBLE, fund_rank DOUBLE, sent_rank DOUBLE, margin_rank DOUBLE,
                composite_score DOUBLE, generated_at TIMESTAMP WITH TIME ZONE,
                PRIMARY KEY(snapshot_id, symbol))"""
        )
        con.execute("DELETE FROM cross_section_factor_snapshot")
        now = datetime.now(tz=TZ)
        rows = []
        all_syms = set(tech_r) | set(fund_r) | set(sent_r) | set(margin_r) | set(composite)
        for s in all_syms:
            rows.append((
                snap["snapshot_id"], s, snap["data_cutoff"],
                tech_r.get(s), fund_r.get(s), sent_r.get(s), margin_r.get(s),
                composite.get(s), now,
            ))
        con.executemany(
            "INSERT INTO cross_section_factor_snapshot VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
        history_rows = [
            (snap["snapshot_id"], snap["data_cutoff"], s,
             tech_r.get(s), fund_r.get(s), sent_r.get(s), margin_r.get(s),
             composite.get(s), now)
            for s in all_syms
        ]
        con.executemany(
            "INSERT OR REPLACE INTO cross_section_factor_snapshot_history VALUES (?,?,?,?,?,?,?,?,?)",
            history_rows,
        )
        valid_composite = np.array(
            [v for v in composite.values() if v is not None and np.isfinite(v)],
            dtype=float,
        )
        if valid_composite.size == 0:
            raise RuntimeError("composite 全为空，无法计算分位数阈值")
        p50, p75, p90, p95 = (
            float(np.quantile(valid_composite, q)) for q in (0.50, 0.75, 0.90, 0.95)
        )
        con.execute(
            "INSERT OR REPLACE INTO cross_section_thresholds VALUES (?,?,?,?,?,?,?,?,?)",
            [snap["data_cutoff"], p50, p75, p90, p95, p90, p75,
             int(valid_composite.size), now],
        )
        con.execute("CHECKPOINT")
        n = con.execute("SELECT COUNT(*) FROM cross_section_factor_snapshot").fetchone()[0]
        hn = con.execute("SELECT COUNT(*) FROM cross_section_factor_snapshot_history").fetchone()[0]
        print(f"写入 cross_section_factor_snapshot: {n} 只 (history 累计 {hn})")
        print(
            f"阈值 P50={p50:.3f} P75={p75:.3f} P90={p90:.3f} P95={p95:.3f} "
            f"(n={valid_composite.size})  强买≥P90 可买≥P75"
        )
        coverage_strong = float((valid_composite >= p90).mean())
        coverage_buy = float((valid_composite >= p75).mean())
        print(f"覆盖: 强买 {coverage_strong:.1%} / 可买 {coverage_buy:.1%}")
        # 抽样
        sample = con.execute(
            "SELECT symbol, tech_rank, fund_rank, sent_rank, margin_rank, composite_score "
            "FROM cross_section_factor_snapshot WHERE composite_score IS NOT NULL "
            "ORDER BY composite_score DESC LIMIT 5"
        ).fetchall()
        print("composite 最高 5 只:")
        def fmt(value):
            return "None" if value is None else f"{value:.3f}"

        for r in sample:
            print(f"  {r[0]} tech={fmt(r[1])} fund={fmt(r[2])} sent={fmt(r[3])} margin={fmt(r[4])} composite={fmt(r[5])}")
    finally:
        con.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
