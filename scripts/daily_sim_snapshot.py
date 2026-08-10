"""每日模拟盘全天观察快照脚本（2026-08 盘前盘后全时段观察表）

用途：盘前Top20候选池（全市场扫描+排名质量评估），全天12时点跟踪。

核心语义：
  - V1（09:10）冻结的Top20 = 全市场扫描的自然输出（0硬条件，按扫描器
    综合分排名前20）—— 这是"评估全市场扫描和排名质量"的基准池，
    之后任何时点不得更换标的。
  - 每个时点对该池逐只研究（五维+决策），输出12字段快照并存档。
  - V1快照永不覆盖（全天评价的原始基准）。

用法:
  python scripts/daily_sim_snapshot.py --timepoint v1
  （时点: v1盘前冻结 / v2竞价后 / v3首5分钟 / c10盘中 / c1030趋势 / am收盘 /
          pm13午后 / pm14趋势 / pm1430尾盘 / vfinal尾盘冻结 / close收盘 / review复盘）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports" / "sim_daily"
TOP_N = 20

TIMEPOINT_ZH = {
    "pre": "盘前准备（确定标的）",
    "bid": "竞价观察",
    "v1": "盘前决策快照V1（基准）",
    "v2": "竞价后快照V2",
    "v3": "首根5分钟线快照V3",
    "c10": "第一次盘中复核",
    "c1030": "趋势稳定性复核",
    "am": "上午收盘快照",
    "pm": "下午首5分钟快照",
    "pm14": "午后趋势复核",
    "pm1430": "尾盘决策复核",
    "vfinal": "冻结尾盘建议",
    "close": "收盘快照",
    "review": "盘后复盘",
}


def _now() -> datetime:
    return datetime.now(tz=TZ)


def _state_path(d: datetime) -> Path:
    return REPORTS_DIR / d.strftime("%Y%m%d") / "state.json"


def _snapshot_path(d: datetime, tp: str) -> Path:
    return REPORTS_DIR / d.strftime("%Y%m%d") / f"snapshot_{tp}.json"


def _load_state(d: datetime) -> dict:
    p = _state_path(d)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"frozen_date": d.strftime("%Y-%m-%d"), "top20": [], "frozen_at": ""}


def _save_state(d: datetime, state: dict) -> None:
    p = _state_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _freeze_top20(state: dict) -> list:
    """按用户选股规则冻结Top20观察池（2026-08-03 EOD_SCAN 明日趋势规则，当日仅一次）

    三组观察池设计（用户2026-08-03确认，不硬凑）：
      命中组：严格命中规则（现价>20日线>60日线 且 技术分>0.3）→ 全部保留
      临界组：接近命中（均线多头或技术分>0.3 仅满足其一）→ 取至多8只
      对照组：明显不满足规则（现价<60日线等）→ 补足到20只
    意义：命中组验证规则命中是否真强；临界组验证规则边界；对照组验证规则区分度。
    """
    if state["top20"]:
        return state["top20"]
    print("（首次运行：按选股规则冻结Top20观察池...）")
    print("（数据源：上一交易日盘后收盘快照，非盘中实时）")
    import duckdb
    from trading.research.technical.analysis import technical_signal
    from trading.scanner.production_partition import (
        CandidateLayer,
        classify_daily_candidate,
    )
    from scripts.analyze_stock import load_bars

    con = duckdb.connect(str(__import__('pathlib').Path(__file__).resolve().parents[1] / "database" / "hermes_opc.duckdb"),
                         read_only=True)
    # 找最近一条 Tushare 盘后快照（cutoff ≤ 现在，作为"上一交易日收盘"）
    today = _now().strftime("%Y-%m-%d")
    row = con.execute("""
        SELECT snapshot_id, data_cutoff FROM market_snapshot_runs
        WHERE provider='Tushare' AND CAST(data_cutoff AS VARCHAR) < ?
        ORDER BY data_cutoff DESC LIMIT 1
    """, [today]).fetchone()
    if not row:
        row = con.execute("""
            SELECT snapshot_id, data_cutoff FROM market_snapshot_runs
            WHERE provider='Tushare' ORDER BY data_cutoff DESC LIMIT 1
        """).fetchone()
    snap_id, snap_cutoff = row
    print(f"（盘后快照: {snap_id[:18]} 截止 {str(snap_cutoff)[:19]}）")

    # 规则1：粗筛池（用盘后收盘快照，非盘中）
    rows = con.execute("""
        SELECT i.symbol, i.price, i.change_pct, i.volume, i.turnover_rate, i.is_suspended,
               u.short_name
        FROM market_snapshot_items i
        LEFT JOIN stock_universe u ON i.symbol = u.symbol
        WHERE i.snapshot_id=?
    """, [snap_id]).fetchall()
    pool = []
    for sym, price, pct, volume, to, suspended, short_name in rows:
        if suspended:
            continue
        if pct is None or price is None or volume is None:
            continue
        if pct <= 0 or not (10 <= price <= 200):
            continue
        # 成交额 = 成交量(手) × 100 × 价格(元) → 元；要求 ≥3亿元（与盘中口径一致）
        amount_yuan = (volume or 0) * 100 * price
        if amount_yuan < 30000_0000:
            continue
        pool.append({"symbol": sym, "name": short_name or sym, "price": price,
                     "pct_chg": pct, "amount_wan": amount_yuan, "turnover": to or 0})
    pool.sort(key=lambda x: -x["amount_wan"])
    print(f"（盘前粗筛池: {len(pool)}只 → 取成交额前30研究分组）")

    # 规则2-3：技术分+均线结构 → 分三组（用库内日线K线）
    hit_group, crit_group, ctrl_group = [], [], []
    for q in pool[:30]:
        try:
            sym_code = q["symbol"].split(".")[0]
            bars = load_bars(con, sym_code)
            if not bars or len(bars) < 30:
                continue
            sig = technical_signal(bars)
            closes = [b.close for b in sorted(bars, key=lambda x: x.trade_date)]
            sma20 = sum(closes[-20:]) / 20
            sma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else sum(closes) / len(closes)
            price = closes[-1]
            score = sig.score
            item = {"symbol": q["symbol"], "name": q["name"], "score": score, "price": price}
            layer = classify_daily_candidate(
                price=price,
                sma20=sma20,
                sma60=sma60,
                technical_score=score,
            )
            if layer == CandidateLayer.CORE:
                hit_group.append(item)
            elif layer == CandidateLayer.NEAR:
                crit_group.append(item)
            else:
                ctrl_group.append(item)
        except Exception:
            continue
    con.close()

    # 排序：命中组按技术分降序；临界组按技术分降序；对照组按技术分降序
    hit_group.sort(key=lambda x: -x["score"])
    crit_group.sort(key=lambda x: -x["score"])
    ctrl_group.sort(key=lambda x: -x["score"])

    # 组装：命中组全部 + 临界组至多8 + 对照组补足到20（带盘后收盘价作为 price_hint）
    frozen = [{"symbol": h["symbol"], "name": h["name"], "group": "命中组",
               "price": h["price"]} for h in hit_group]
    for c in crit_group[:8]:
        if len(frozen) >= TOP_N:
            break
        frozen.append({"symbol": c["symbol"], "name": c["name"], "group": "临界组",
                       "price": c["price"]})
    for c in ctrl_group:
        if len(frozen) >= TOP_N:
            break
        frozen.append({"symbol": c["symbol"], "name": c["name"], "group": "对照组",
                       "price": c["price"]})

    # 极端兜底（全市场命中/临界都不足时，用扫描器排名补足，标注对照组）
    if len(frozen) < TOP_N:
        from trading.scanner.service import MarketScannerService
        from trading.scanner.schemas import ScannerScanRequest
        from trading.schemas import AnalysisMode
        from datetime import datetime
        print(f"⚠️ 命中{len(hit_group)}+临界{len(crit_group)}不足，扫描器排名补足对照组")
        try:
            svc = MarketScannerService()
            result = svc.scan(ScannerScanRequest(
                query="全A股前20只",
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=datetime.now().astimezone(),
                top_n=TOP_N,
                persist_run=False,
            ))
            cands = getattr(result, "candidates", []) or []
            seen = {f["symbol"] for f in frozen}
            for c in cands:
                if len(frozen) >= TOP_N:
                    break
                sym = c.symbol if not isinstance(c, dict) else c.get("symbol", "")
                name = (c.short_name if not isinstance(c, dict) else c.get("short_name", "")) or ""
                if sym not in seen:
                    frozen.append({"symbol": sym, "name": name, "group": "对照组"})
                    seen.add(sym)
        except Exception as exc:
            print(f"（扫描器补足失败: {exc}）")

    state["top20"] = frozen
    state["frozen_at"] = _now().isoformat()
    _save_state(_now(), state)
    from collections import Counter
    groups = Counter(f["group"] for f in frozen)
    print(f"（冻结完成: 命中{len(hit_group)} 临界{len(crit_group)} 对照{len(ctrl_group)} → 观察池{len(frozen)}只: "
          + " ".join(f"{k}{v}只" for k, v in groups.items()) + "）")
    return frozen


def _research_one(symbol: str, name: str, price_hint: float | None = None) -> dict:
    """单只研究：五维+决策 → 12字段快照

    price_hint: 兜底价（快照里找不到时才用）
    修复：优先从实时快照取最新价（不用冻结价）
    """
    from scripts.intraday_pipeline import IntradayPipeline
    pipe = IntradayPipeline()
    quotes, meta = pipe.realtime.get_snapshot()
    if not quotes:
        pipe.realtime.refresh_once()
        quotes, meta = pipe.realtime.get_snapshot()
    q = None
    if quotes:
        q = next((x for x in quotes if x.symbol == symbol), None)
        if q is None:
            q = next((x for x in quotes if x.symbol.upper() == symbol.upper()), None)
    realtime = {
        "symbol": symbol, "name": name,
        "price": q.price if q else (price_hint or 0),
        "pct_chg": q.pct_chg if q else 0,
        "volume_ratio": q.volume_ratio if q else 0,
        "turnover": q.turnover if q else 0,
        "amount_wan": q.amount_wan if q else 0,
        "high": q.high if q else 0, "low": q.low if q else 0,
        "open": q.open if q else 0, "prev_close": q.prev_close if q else 0,
    }
    r = pipe._research_one(symbol.split(".")[0], realtime)
    if r.get("status") != "OK":
        return {
            "symbol": symbol, "name": name, "status": r.get("status", "ERROR"),
            "当前动作": "回避", "总评分": None, "veto": r.get("veto", ""),
        }
    act = pipe.candidate_action_for(r)
    is_ = r["intraday_strength"]
    sq = r["swing_quality"]
    return {
        "symbol": symbol, "name": name, "status": "OK",
        "决策时间": _now().strftime("%H:%M"),
        "当前动作": act["action"],
        "总评分": round(r.get("formal_score", 0), 3),
        "五维结论": {
            "技术": r.get("technical_score"), "基本": r.get("fundamental_score"),
            "情绪": None, "政策": None, "资金": None,
        },
        "盘中强度": is_.get("level"), "波段质量": sq.get("level"),
        "仓位建议": "模拟观察",
        "参考价格": r.get("price"),
        "止损止盈": None,
        "VETO状态": r.get("veto", "NO_VETO"),
        "相比上次变化": "",
        "变化原因": "",
        "有效期限": "当日",
    }


def _compare_prev(prev: dict, cur: dict) -> dict:
    """与上一时点对比变化"""
    changes = []
    if prev.get("当前动作") != cur.get("当前动作"):
        changes.append(f"动作:{prev.get('当前动作')}→{cur.get('当前动作')}")
    if prev.get("总评分") is not None and cur.get("总评分") is not None:
        d = cur["总评分"] - prev["总评分"]
        if abs(d) >= 0.05:
            changes.append(f"评分:{prev['总评分']:+.2f}→{cur['总评分']:+.2f}({d:+.2f})")
    if prev.get("参考价格") and cur.get("参考价格"):
        p = cur["参考价格"] - prev["参考价格"]
        if abs(p) >= 0.1:
            changes.append(f"价格:{prev['参考价格']:.2f}→{cur['参考价格']:.2f}")
    cur["相比上次变化"] = "；".join(changes) if changes else "无变化"
    return cur


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timepoint", required=True, choices=list(TIMEPOINT_ZH.keys()))
    parser.add_argument("--max-research", type=int, default=TOP_N)
    args = parser.parse_args()

    tp = args.timepoint
    now = _now()
    print(f"📌 {TIMEPOINT_ZH[tp]} | {now.strftime('%Y-%m-%d %H:%M')}")

    state = _load_state(now)
    frozen = _freeze_top20(state)
    _save_state(now, state)
    from collections import Counter
    groups = Counter(f.get("group", "未分组") for f in frozen)
    print(f"观察池（冻结{len(frozen)}只，不可更改）: " + " ".join(f"{k}{v}只" for k, v in groups.items()))
    for f in frozen[:10]:
        print(f"  [{f.get('group','')}] {f['name']}({f['symbol'].split('.')[0]})")
    if len(frozen) > 10:
        print(f"  ...共{len(frozen)}只")

    # 逐只研究
    results = []
    for f in frozen:
        try:
            r = _research_one(f["symbol"], f["name"], price_hint=f.get("price"))
            r["观察组"] = f.get("group", "未分组")
            results.append(r)
        except Exception as exc:
            results.append({"symbol": f["symbol"], "name": f["name"],
                            "status": "ERROR", "error": str(exc)[:50]})
        time.sleep(0.1)

    # 对比上一时点
    tp_order = ["v1", "v2", "v3", "c10", "c1030", "am", "pm", "pm14", "pm1430",
                "vfinal", "close", "review"]
    prev_tp = None
    for t in tp_order:
        if t == tp:
            break
        prev_tp = t
    if prev_tp:
        prev_path = _snapshot_path(now, prev_tp)
        if prev_path.exists():
            prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
            prev_map = {r["symbol"]: r for r in prev_data.get("results", [])}
            for r in results:
                if r["symbol"] in prev_map:
                    _compare_prev(prev_map[r["symbol"]], r)

    # 保存快照（V1 永不覆盖）
    snap_path = _snapshot_path(now, tp)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_data = {"timepoint": tp, "timepoint_zh": TIMEPOINT_ZH[tp],
                 "generated_at": now.isoformat(), "results": results}
    if tp == "v1" and snap_path.exists():
        print("⚠️ V1已存在，不覆盖（基准快照）")
    else:
        snap_path.write_text(json.dumps(snap_data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # 输出摘要
    ok = [r for r in results if r.get("status") == "OK"]
    act_counts: dict[str, int] = {}
    for r in ok:
        a = r.get("当前动作", "未知")
        act_counts[a] = act_counts.get(a, 0) + 1
    print(f"\n研究完成: {len(ok)}/{len(results)} | 动作分布: "
          + " ".join(f"{k}×{v}" for k, v in sorted(act_counts.items(), key=lambda x: -x[1])))
    print("\n=== 各标的快照 ===")
    for r in ok:
        chg = r.get("相比上次变化", "")
        grp = r.get("观察组", "")
        print(f"[{grp}] {r['name']}({r['symbol'].split('.')[0]}) {r.get('当前动作')} "
              f"评分{r.get('总评分'):+.2f} 价{r.get('参考价格'):.2f}"
              + (f" | 变化:{chg}" if chg and chg != "无变化" else ""))
    # 按组统计（复盘验证规则）
    from collections import Counter
    grp_act = {}
    for r in ok:
        g = r.get("观察组", "未分组")
        grp_act.setdefault(g, Counter())[r.get("当前动作", "未知")] += 1
    if grp_act:
        print("\n=== 分组动作统计（验证规则） ===")
        for g, cnt in grp_act.items():
            print(f"  {g}: " + " ".join(f"{a}×{n}" for a, n in cnt.most_common()))
    vetos = [r for r in ok if r.get("VETO状态") != "NO_VETO"]
    if vetos:
        print(f"\n⚠️ 风控拦截 {len(vetos)}只: " + "、".join(
            f"{r['name']}({r.get('VETO状态')})" for r in vetos))

    print(f"\n快照已存: {snap_path}")


if __name__ == "__main__":
    main()
