#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟观察池 —— 优化版验证工具（只读，不改动生产链/冻结池/定时任务）

解决的问题（相对原观察池"命中组 vs 对照组"裸比较）：
  1. 加【中性基准】全市场等权当日收益，命中组与市场比，而非只跟被刻意挑弱的对照组比；
  2. 【组间口径对称】可选统一门槛（三组都按五维综合分过滤），消除"命中组剔低分/对照组不剔"的不对称；
  3. 【多期聚合】逐日 → 跨日汇总（均值/中位/胜率/配对 t 检验），单日不结论；
  4. 【同源同口径】池内个股与基准都取自同一来源（快照明细 pct_chg，按 trade_date），避免口径混用。

数据源：
  - 冻结池：reports/sim_daily/<YYYYMMDD>/state.json（frozen_date + top20[{symbol,name,group}]）
  - 综合分：reports/sim_daily/<YYYYMMDD>/snapshot_*.json 的 results[].总评分
  - 收益基准：database/hermes_opc.duckdb → market_snapshot_items
              （json_extract(payload,'$.trade_date'/'$.pct_chg')，全市场 ~5500 只/日）

口径说明：
  - 观察池于 D 日盘前冻结（用 D-1 盘后数据）；本工具衡量的是 D 日当日收益
    （= 快照明细 trade_date=D 的 pct_chg），即"盘前冻结 → 当日表现"。
  - 该工具是【事后评估】，不参与冻结，不写回任何生产表。

用法：
  ./.venv/Scripts/python.exe scripts/sim_validation.py
  ./.venv/Scripts/python.exe scripts/sim_validation.py --gate        # 三组统一用综合分≥0.3 过滤
  ./.venv/Scripts/python.exe scripts/sim_validation.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "reports" / "sim_daily"
DB = ROOT / "database" / "hermes_opc.duckdb"

HIT_SCORE_MIN = 0.3  # 命中组"有效"综合分门槛（原实现仅对命中组用；--gate 时对三组统一用）
BUY_MIN = 0.20       # 建仓/候选线（候选级"强势研究候选"阈值 formal≥0.2；生产正式建仓线0.22）
# 观察池用的是"候选级动作"（intraday_pipeline.candidate_action_for；规则二：盘中不得直接输出建仓结论）。
# 其中"买入意图"对应动作 = 强势研究候选；正式链的建仓/加仓类也一并纳入以便复用。
BUY_ACTIONS = {"强势研究候选", "建仓", "加仓", "强烈建仓", "增持", "买入", "买入建仓"}


def is_buy_action(action, score, veto) -> bool:
    """策略组判据：无VETO 且 动作∈买入意图集 且 五维综合分≥BUY_MIN。
    供 daily_sim_snapshot.py 与验证工具共用，保证口径一致。"""
    if veto not in (None, "", "NO_VETO"):
        return False
    if action not in BUY_ACTIONS:
        return False
    if score is None or score < BUY_MIN:
        return False
    return True


# 用户硬性口径（yiyi-investment-assistant §十七）：推荐/策略组必须排除
# 创业板(300/301)、科创板(688)、北交所(4xx/8xx/920)、ST。
# 2026-10-13：策略组(五维建仓)此前未按其口径过滤，曾把创业板票(300209行云科技)
# 计入"可执行建仓信号"，与用户口径冲突——此处统一收口。
EXCLUDED_CODE_PREFIXES = ("300", "301", "688", "920")


def is_recommendable(symbol: str | None, name: str | None = None) -> bool:
    """是否属于用户可参与范围（非创业板/科创板/北交所/ST）。"""
    code = (symbol or "").split(".")[0]
    if not code:
        return False
    if code.startswith(EXCLUDED_CODE_PREFIXES) or code[:1] in ("4", "8"):
        return False
    if name and "ST" in name.upper():
        return False
    return True


def _load_pools() -> dict[str, dict]:
    """返回 {trade_date_str(YYYY-MM-DD): {"frozen_at":.., "stocks":[{symbol,group}]}}"""
    out: dict[str, dict] = {}
    for d in sorted(SIM_DIR.glob("2026*")):
        if not d.is_dir() or len(d.name) != 8:
            continue
        sj = d / "state.json"
        if not sj.exists():
            continue
        try:
            st = json.loads(sj.read_text(encoding="utf-8"))
        except Exception:
            continue
        top = st.get("top20") or []
        if not top:
            continue
        fd = st.get("frozen_date") or f"{d.name[:4]}-{d.name[4:6]}-{d.name[6:]}"
        stocks = [{"symbol": x.get("symbol"), "group": x.get("group") or "未分组"} for x in top]
        out[fd] = {"frozen_at": st.get("frozen_at"), "stocks": stocks}
    return out


def _load_pool_fields(date_dir: Path) -> dict[str, dict]:
    """从当日任一含 results 的快照取 {symbol: {score, action, veto}}。"""
    best: dict[str, dict] = {}
    for f in sorted(date_dir.glob("snapshot_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        res = d.get("results") or []
        for r in res:
            s = r.get("总评分")
            if s is not None and r.get("symbol"):
                best.setdefault(r["symbol"], {
                    "score": s, "action": r.get("当前动作"), "veto": r.get("VETO状态"),
                })
        if best:
            break
    return best


def _load_market() -> tuple[dict[str, dict[str, float]], dict[str, float], dict[str, int]]:
    """从快照明细构建按交易日索引的全市场 pct_chg。
    返回 (by_date{sym:chg}, mkt_mean{date:mean}, mkt_n{date:count})。"""
    con = duckdb.connect(str(DB), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT json_extract_string(payload_json,'$.trade_date') AS td,
                   symbol,
                   TRY_CAST(json_extract_string(payload_json,'$.pct_chg') AS DOUBLE) AS chg
            FROM market_snapshot_items
            WHERE json_extract_string(payload_json,'$.pct_chg') IS NOT NULL
            """
        ).fetchall()
    finally:
        con.close()
    by_date: dict[str, dict[str, float]] = {}
    for td, sym, chg in rows:
        if td is None or chg is None:
            continue
        td = f"{td[:4]}-{td[4:6]}-{td[6:]}"
        by_date.setdefault(td, {})[sym] = chg
    mkt_mean = {td: (sum(v.values()) / len(v)) for td, v in by_date.items() if v}
    mkt_n = {td: len(v) for td, v in by_date.items()}
    return by_date, mkt_mean, mkt_n


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _paired_t(diffs: list[float]) -> tuple[float, float]:
    """返回 (mean, t)。样本 <2 或方差 0 时 t=nan。"""
    n = len(diffs)
    if n < 2:
        return (_mean(diffs), float("nan"))
    m = _mean(diffs)
    sd = statistics.stdev(diffs)
    if sd == 0:
        return (m, float("nan"))
    return (m, m / (sd / math.sqrt(n)))


def _fwd(by_date: dict, dates: list, start: str, sym: str, horizon: int):
    """前向 horizon 个交易日累计收益(%)，从 start 日起算。数据缺失返回 None。"""
    try:
        i0 = dates.index(start)
    except ValueError:
        return None
    days = dates[i0:i0 + horizon]
    if len(days) < horizon:
        return None
    prod = 1.0
    for d in days:
        v = by_date.get(d, {}).get(sym)
        if v is None:
            return None
        prod *= (1.0 + v / 100.0)
    return (prod - 1.0) * 100.0


def _fwd_bench(mkt_mean: dict, dates: list, start: str, horizon: int):
    """全市场等权基准的前向 horizon 日累计收益(%)（用日序列复利近似）。"""
    try:
        i0 = dates.index(start)
    except ValueError:
        return None
    days = dates[i0:i0 + horizon]
    if len(days) < horizon:
        return None
    prod = 1.0
    for d in days:
        v = mkt_mean.get(d)
        if v is None:
            return None
        prod *= (1.0 + v / 100.0)
    return (prod - 1.0) * 100.0


def _limit_pct(sym: str) -> float:
    """涨停幅度：创业板/科创板 20%，北交所 30%，其余 10%。"""
    c = sym.split(".")[0]
    if c[:3] in ("300", "301", "688"):
        return 20.0
    if c[:2] in ("43", "83", "87", "88", "92"):
        return 30.0
    return 10.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true", help="三组统一用综合分≥0.3 过滤（口径对称）")
    ap.add_argument("--horizon", type=int, default=1, help="前向收益天数（T+N，默认1）")
    ap.add_argument("--json", default="", help="把结构化结果写到此 JSON")
    args = ap.parse_args()
    horizon = max(1, args.horizon)

    pools = _load_pools()
    by_date, mkt_mean, mkt_n = _load_market()

    # 交易日序列 → 次日映射（策略组用 T+1 收益，避免同日 look-ahead）
    _ds = sorted(by_date)
    next_date = {_ds[i]: _ds[i + 1] for i in range(len(_ds) - 1)}

    daily = []          # 每日一行
    skipped = []
    buyblocked = 0
    for day, meta in sorted(pools.items()):
        if day not in by_date:
            skipped.append((day, "无当日收益数据(交易日历/快照缺失)"))
            continue
        fields = _load_pool_fields(SIM_DIR / day.replace("-", ""))
        grp_vals: dict[str, list[float]] = {}
        for s in meta["stocks"]:
            sym, g = s["symbol"], s["group"]
            # 可执行口径：当日涨停(≥涨停线-0.2)视为买不进 → 剔除
            dm = by_date[day].get(sym)
            if dm is not None and dm >= _limit_pct(sym) - 0.2:
                buyblocked += 1
                continue
            chg = _fwd(by_date, _ds, day, sym, horizon)
            if chg is None:
                continue
            f = fields.get(sym) or {}
            if args.gate:
                sc = f.get("score")
                if sc is None or sc < HIT_SCORE_MIN:
                    continue  # 三组统一门槛（对称）
            grp_vals.setdefault(g, []).append(chg)
        if not grp_vals.get("命中组"):
            skipped.append((day, "命中组无有效数据"))
            continue
        # 策略组（D+1 起、前向 horizon 日）：动作/评分是 D 日盘中信号 → 避免同日 look-ahead
        nxt = next_date.get(day)
        strat_vals: list[float] = []
        if nxt:
            for s in meta["stocks"]:
                sym = s["symbol"]
                f = fields.get(sym) or {}
                if is_buy_action(f.get("action"), f.get("score"), f.get("veto")):
                    c1 = _fwd(by_date, _ds, nxt, sym, horizon)
                    if c1 is not None:
                        strat_vals.append(c1)
        _b0 = _fwd_bench(mkt_mean, _ds, day, horizon)
        _b1 = _fwd_bench(mkt_mean, _ds, nxt, horizon) if nxt else None
        row = {
            "date": day,
            "bench": (_b0 if _b0 is not None else float("nan")),
            "n_mkt": mkt_n.get(day, 0),
            "bench_t1": (_b1 if _b1 is not None else float("nan")),
        }
        for g in ("命中组", "临界组", "对照组"):
            v = grp_vals.get(g, [])
            row[g] = _mean(v) if v else float("nan")
            row[f"{g}_n"] = len(v)
        row["策略组"] = _mean(strat_vals) if strat_vals else float("nan")
        row["策略组_n"] = len(strat_vals)
        daily.append(row)

    # ---- 汇总 ----
    def col(g, kind="mean"):
        xs = [r[g] for r in daily if not math.isnan(r[g])]
        if kind == "mean":
            return _mean(xs)
        if kind == "median":
            return statistics.median(xs) if xs else float("nan")
        return xs

    lines = []
    lines.append("# 模拟观察池 —— 优化版多期验证报告")
    lines.append("")
    lines.append(f"- 口径：盘前冻结(D，用D-1数据) → 前向 **T+{horizon}** 累计收益；已剔除当日涨停(买不进)个股")
    lines.append(f"- 门槛：{'三组统一 综合分≥0.3（对称）' if args.gate else '原始口径（命中组=冻结池命中组）'}")
    lines.append(f"- 有效交易日：{len(daily)} 天；跳过：{len(skipped)} 天；因涨停剔除样本：{buyblocked} 个")
    lines.append("")

    lines.append("## 一、逐日明细（%）")
    lines.append("")
    lines.append("| 日期 | 命中组 | 临界组 | 对照组 | 策略组(T+1) | 全市场等权 | 命中-市场 | 策略-市场(T+1) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in daily:
        def f(v):
            return "—" if math.isnan(v) else f"{v:+.2f}"
        lines.append(
            f"| {r['date']} | {f(r['命中组'])}({r['命中组_n']}) | {f(r['临界组'])}({r['临界组_n']}) "
            f"| {f(r['对照组'])}({r['对照组_n']}) | {f(r['策略组'])}({r['策略组_n']}) | {f(r['bench'])} | "
            f"{f(r['命中组']-r['bench'])} | {f(r['策略组']-r['bench_t1'])} |"
        )
    lines.append("")

    lines.append("## 二、跨期汇总")
    lines.append("")
    def _f3(v):
        return "—" if (v != v) else f"{v:+.3f}"
    lines.append("| 指标 | 命中组 | 临界组 | 对照组 | 策略组 | 全市场等权 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(
        f"| 平均T+{horizon}收益 | {_f3(col('命中组'))} | {_f3(col('临界组'))} "
        f"| {_f3(col('对照组'))} | {_f3(col('策略组'))} | {_f3(_mean([r['bench'] for r in daily]))} |"
    )
    lines.append(
        f"| 中位T+{horizon}收益 | {_f3(col('命中组','median'))} | {_f3(col('临界组','median'))} "
        f"| {_f3(col('对照组','median'))} | {_f3(col('策略组','median'))} | {_f3(statistics.median([r['bench'] for r in daily]) if daily else float('nan'))} |"
    )
    # 胜率（相对市场）
    def winrate(g):
        xs = [r[g] - r["bench"] for r in daily if not math.isnan(r[g]) and not math.isnan(r["bench"])]
        return (sum(1 for x in xs if x > 0), len(xs))
    for g in ("命中组", "临界组", "对照组", "策略组"):
        bcol = "bench_t1" if g == "策略组" else "bench"
        xs = [r[g] - r[bcol] for r in daily if not math.isnan(r[g]) and not math.isnan(r[bcol])]
        w = sum(1 for x in xs if x > 0)
        lbl = f"{g}(T+1)" if g == "策略组" else g
        lines.append(f"| {lbl}跑赢市场天数 | {w}/{len(xs)} | | | | |")
    lines.append("")

    # 配对 t 检验：命中组-市场 / 命中组-对照组
    lines.append("## 三、配对检验（命中组相对基准，逐日配对）")
    lines.append("")
    d_mkt = [r["命中组"] - r["bench"] for r in daily if not math.isnan(r["命中组"]) and not math.isnan(r["bench"])]
    d_ctrl = [r["命中组"] - r["对照组"] for r in daily if not math.isnan(r["命中组"]) and not math.isnan(r["对照组"])]
    d_strat = [r["策略组"] - r["bench_t1"] for r in daily if not math.isnan(r["策略组"]) and not math.isnan(r["bench_t1"])]
    m1, t1 = _paired_t(d_mkt)
    m2, t2 = _paired_t(d_ctrl)
    m3, t3 = _paired_t(d_strat)
    def tt(t):
        return "—" if (t != t) else f"{t:+.2f}"
    lines.append(f"- 命中组 − 全市场：均值 {m1:+.3f}%/日，t={tt(t1)}（n={len(d_mkt)}）")
    lines.append(f"- 命中组 − 对照组：均值 {m2:+.3f}%/日，t={tt(t2)}（n={len(d_ctrl)}）")
    lines.append(f"- 策略组 − 全市场：均值 {m3:+.3f}%/日，t={tt(t3)}（n={len(d_strat)}）")
    lines.append("")
    lines.append("判读门槛（经验）：|t|≥2 且样本≥20 天才勉强可称“有区分度”；单日/小样本一律不结论。")
    lines.append("")
    lines.append("## 四、数据边界与说明")
    lines.append("")
    lines.append(f"- 收益数据覆盖：{min(by_date)} ~ {max(by_date)}（共 {len(by_date)} 个交易日）")
    lines.append("- 基准 = 全市场等权收益（按日序列复利近似 T+N；含 ST/停牌股按有数据者计，未剔除）。")
    lines.append("- 可执行口径近似：按“当日涨幅≥涨停线-0.2”剔除买不进的个股（无开盘价，属收盘近似）；未计成本/滑点。")
    if skipped:
        lines.append("")
        lines.append("跳过日：")
        for d, why in skipped:
            lines.append(f"- {d}: {why}")

    text = "\n".join(lines)
    out_md = SIM_DIR / f"VALIDATION_{__import__('datetime').date.today().strftime('%Y%m%d')}_T{horizon}{'_gate' if args.gate else ''}.md"
    out_md.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[已写] {out_md}")
    if args.json:
        Path(args.json).write_text(json.dumps({"daily": daily, "skipped": skipped}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[已写] {args.json}")


if __name__ == "__main__":
    main()
