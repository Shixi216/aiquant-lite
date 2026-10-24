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
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports" / "sim_daily"
TOP_N = 50  # 2026-08-26 扩容：20→50（原命中组仅2~6只，样本量不足）

TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _tencent_prices(symbols: list[str]) -> dict[str, dict]:
    """腾讯批量直拉实时价（修复 2026-08-26）

    背景：原实现依赖 MarketRealtimeSnapshotService 进程内存快照，
    cron 独立进程内存为空；触发后台刷新时 _symbols() 用 open_database
    访问主库被 DatabaseOwnershipError 拦截（主库仅允许 Router 进程直连），
    刷新 2ms 失败 → 永远拿不到实时价 → 价格退回冻结价 → 收益率恒为 0。

    本函数绕过该链路：直接请求腾讯行情接口，一次拉取观察池全部代码。
    返回 {symbol: {price, pct_chg, volume_ratio, turnover, amount_wan,
                   high, low, open, prev_close}}
    """
    codes: list[str] = []
    for s in symbols:
        code = s.split(".")[0]
        # 2026-09-29 第二批S2修复：补北交所前缀，原实现 .BJ 被拼成 sz
        # 导致腾讯接口返回空、实时价缺失。
        if s.upper().endswith(".SH"):
            prefix = "sh"
        elif s.upper().endswith(".BJ"):
            prefix = "bj"
        else:
            prefix = "sz"
        codes.append(prefix + code)
    out: dict[str, dict] = {}
    try:
        url = "https://qt.gtimg.cn/q=" + ",".join(codes)
        req = urllib.request.Request(url, headers=TENCENT_HEADERS)
        raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="ignore")
        for line in raw.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            payload = line.split("=", 1)[1].strip().strip('"')
            parts = payload.split("~")
            if len(parts) < 40:
                continue
            tcode = parts[2]  # 6位代码
            quote = {
                "price": _to_float(parts[3]),
                "prev_close": _to_float(parts[4]),
                "open": _to_float(parts[5]),
                "high": _to_float(parts[33]),
                "low": _to_float(parts[34]),
                "pct_chg": _to_float(parts[32]),
                "amount_wan": _to_float(parts[37]),
                "turnover": _to_float(parts[38]),
                "volume_ratio": _to_float(parts[49]) if len(parts) > 49 else 0.0,
            }
            for s in symbols:
                if s.split(".")[0] == tcode:
                    out[s] = quote
                    break
    except Exception as exc:
        print(f"（腾讯直拉失败: {exc}）")
    return out

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


def _market_benchmark() -> dict:
    """中性基准（同日大盘）—— 优化 2026-10-08。

    取腾讯指数当日涨跌作为"市场基准"，让观察池命中组/策略组与市场比较，
    而不是只跟被刻意挑弱的对照组比。主基准=中证全指(全A最广)，备=沪深300/上证。
    失败返回 {}，不影响主流程。
    """
    codes = [("sh000985", "中证全指"), ("sh000300", "沪深300"), ("sh000001", "上证指数")]
    try:
        url = "https://qt.gtimg.cn/q=" + ",".join(c for c, _ in codes)
        req = urllib.request.Request(url, headers=TENCENT_HEADERS)
        raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
        out: dict[str, float] = {}
        for line in raw.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            _k, payload = line.split("=", 1)
            parts = payload.strip().strip('"').split("~")
            if len(parts) > 32:
                out[parts[1]] = _to_float(parts[32])
        return out
    except Exception:
        return {}


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


def _partition_by_composite(pool: list, comp: dict, buy_q: float, p50: float):
    """按四维横截面评分把候选池切三组（2026-10-08 改造，纯函数，便于单测）。

    命中组 = composite ≥ buy_q；临界组 = p50 ≤ composite < buy_q；对照组 = 其余/无截面分。
    """
    hit, crit, ctrl = [], [], []
    for q in pool:
        sym = q["symbol"]
        cs = comp.get(sym)
        item = {"symbol": sym, "name": q["name"],
                "score": (cs if cs is not None else -9.9), "price": q["price"]}
        if cs is None:
            ctrl.append(item)
        elif cs >= buy_q:
            hit.append(item)
        elif cs >= p50:
            crit.append(item)
        else:
            ctrl.append(item)
    return hit, crit, ctrl


def _read_sector_context() -> dict:
    """读最新 sector_start 快照，取当日市场环境 + 强势板块（2026-10-08 加，供长期验证板块维度）。

    数据源: reports/trend_radar/sector_start_<date>.json
            （由 scripts/sector_start.py 每交易日 16:35 产出）。
    读不到则返回空 dict（静默降级，不影响快照）。
    """
    try:
        import glob
        files = sorted(f for f in glob.glob(str(REPORTS_DIR.parent / "trend_radar" / "sector_start_*.json"))
                       if "validation" not in f)
        if not files:
            return {}
        d = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
        return {"as_of": d.get("log_date"), "env": d.get("env"),
                "env_txt": d.get("env_txt"), "strong_sectors": d.get("strong_sectors")}
    except Exception:
        return {}


def _harden_hit_group(hit_list):
    """命中组硬化（2026-10-08 用户要求）：分数达标 ≠ 命中，须再过 决策关 + 交叉验证关。

    决策关：Router 五维(no_fetch) → 动作∈建仓类 且 执行状态 ∉ {禁止追高,已失效,信号失效}
    交叉验证关：近10日龙虎榜区间净额 > -1亿（≤-1亿=硬否决）
    降级项附 reject 原因；任何异常 → 原样保留该只（降级不阻断，冻结安全）。

    返回 (kept, dropped)
    """
    if not hit_list:
        return hit_list, []
    import urllib.request
    import json as _json
    import re as _re
    import os as _os
    ROOT = Path(__file__).resolve().parents[1]

    # 1) 交叉验证数据：近10日龙虎榜区间净额 + 主力资金10日净额（批量按日）
    lhb_net, mf_net = {}, {}
    try:
        tok = _os.environ.get("TUSHARE_TOKEN", "")
        if not tok:
            ef = ROOT / ".env"
            if ef.exists():
                for ln in ef.read_text(encoding="utf-8").splitlines():
                    if ln.startswith("TUSHARE_TOKEN="):
                        tok = ln.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if tok:
            for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
                _os.environ.pop(k, None)
            import tushare as ts
            import tushare.pro.client as _tc
            _o = _tc.DataApi.__init__

            def _p(self, *a, **kw):
                _o(self, *a, **kw)
                self._DataApi__http_url = 'https://api.tushare.pro/dataapi'
            _tc.DataApi.__init__ = _p
            pro = ts.pro_api(tok)
            _today = _now().strftime('%Y%m%d')
            cal = pro.trade_cal(exchange='SSE', is_open='1', end_date=_today)
            tds = sorted(cal[cal['is_open'] == 1]['cal_date'].tolist())[-10:]
            for td in tds:
                try:
                    tl = pro.top_list(trade_date=td)
                    for _, r in tl.iterrows():
                        lhb_net[r['ts_code']] = lhb_net.get(r['ts_code'], 0.0) + float(r.get('net_amount', 0) or 0)
                except Exception:
                    pass
                try:
                    mf = pro.moneyflow(trade_date=td)
                    mf['big'] = (mf['buy_elg_amount'] - mf['sell_elg_amount']) + \
                                (mf['buy_lg_amount'] - mf['sell_lg_amount'])
                    for _, r in mf.iterrows():
                        mf_net[r['ts_code']] = mf_net.get(r['ts_code'], 0.0) + float(r['big'])
                except Exception:
                    pass
    except Exception:
        pass

    REJECT_ACTIONS = {"回避", "清仓", "减仓", "退出"}  # 盘前冻结决策多为"等待确认", 故取"否决式": 剔回避/退出/禁止追高/失效, 不强制"必须建仓"
    BAD_STATUS = {"禁止追高", "已失效", "信号失效"}
    kept, dropped = [], []
    for h in hit_list:
        sym = h["symbol"]
        # 交叉验证关：龙虎榜硬否决
        lhb = lhb_net.get(sym, 0.0) / 1e8
        if lhb <= -1.0:
            h["reject"] = f"龙虎榜区间净卖{lhb:.2f}亿(硬否决)"
            dropped.append(h)
            continue
        # 决策关：Router 五维
        act, st = None, None
        try:
            body = _json.dumps({"symbol": sym, "days": 90, "no_fetch": True,
                                "no_ai": True, "force_refresh": False}).encode()
            req = urllib.request.Request("http://127.0.0.1:8765/v1/research/stock-report",
                                         data=body, headers={"Content-Type": "application/json"})
            rep = _json.loads(urllib.request.urlopen(req, timeout=30).read().decode()).get("report", "")
            ma = _re.search(r'正式动作\(DecisionEngine\): (.+)', rep)
            act = ma.group(1).strip() if ma else None
            ms = _re.search(r'执行状态: (.+)', rep)
            st = ms.group(1).strip() if ms else None
        except Exception:
            pass  # Router 不可用 → 不做决策关（降级保留，不误杀）
        if act is not None:
            if act in REJECT_ACTIONS:
                h["reject"] = f"决策={act}"
                dropped.append(h)
                continue
            if st in BAD_STATUS:
                h["reject"] = f"执行状态={st}"
                dropped.append(h)
                continue
        h["lhb_10d"] = round(lhb, 2)
        h["mf_10d"] = round(mf_net.get(sym, 0.0) / 1e4, 2)  # 万元→亿
        kept.append(h)
    return kept, dropped


def _freeze_top20(state: dict) -> list:
    """按用户选股规则冻结Top50观察池（2026-08-03 EOD_SCAN 明日趋势规则，当日仅一次）

    三组观察池设计（用户2026-08-03确认，不硬凑）：
      命中组：严格命中规则（现价>20日线>60日线 且 技术分>0.3）→ 全部保留
      临界组：接近命中（均线多头或技术分>0.3 仅满足其一）→ 取至多15只
      对照组：明显不满足规则（现价<60日线等）→ 补足到50只
    意义：命中组验证规则命中是否真强；临界组验证规则边界；对照组验证规则区分度。
    2026-08-26 扩容：TOP_N 20→50（原命中组仅2~6只，样本量不足以验证规则），
    研究池 前30→前80，临界组上限 8→15。
    """
    if state["top20"]:
        return state["top20"]
    print("（首次运行：按选股规则冻结Top50观察池...）")
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
    # 2026-08-26 修复：原用字符串比较 data_cutoff < today，当天快照（含时间）
    # 会被排除导致退回更早快照；改日期比较，取不晚于今天的最近快照。
    today = _now().strftime("%Y-%m-%d")
    row = con.execute("""
        SELECT snapshot_id, data_cutoff FROM market_snapshot_runs
        WHERE provider='Tushare' AND CAST(data_cutoff AS DATE) <= CAST(? AS DATE)
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
        # 成交额估算（2026-10-06 修复）：本冻结读取的是 provider='Tushare'
        # 的盘后快照，其 volume 字段口径是"手"（Tushare daily.vol 单位=手），
        # 并未在入库时×100。故真实成交额(元) = volume(手) × 100 × price(元)。
        # 实证：快照内 amount 列单位为千元，amount×1000 ≈ volume×100×price
        # （如 300308：130.99亿 vs 130.66亿）。
        # ⚠️ 2026-09-29 曾误按"volume 已×100=股"去掉×100（M3 修复），导致
        # 成交额低估 100 倍 → 3亿门槛下粗筛池归零 → 冻结观察池为空 →
        # 09-30 起全部快照 results 为空。此处为回退修复。
        amount_yuan = (volume or 0) * 100 * price
        if amount_yuan < 30000_0000:
            continue
        pool.append({"symbol": sym, "name": short_name or sym, "price": price,
                     "pct_chg": pct, "amount_yuan": amount_yuan, "turnover": to or 0})
    pool.sort(key=lambda x: -x["amount_yuan"])
    print(f"（盘前粗筛池: {len(pool)}只 → 取成交额前80研究分组）")

    # 规则2-3（2026-10-08 改造）：命中组改为"四维横截面评分盘前选票"。
    #   命中组 = composite ≥ buy_q（读 cross_section_thresholds，回退0.22）
    #   临界组 = p50 ≤ composite < buy_q
    #   对照组 = composite < p50
    # 数据源：cross_section_factor_snapshot（最新截面，对应上一交易日）。读不到则回退旧技术规则。
    hit_group, crit_group, ctrl_group = [], [], []
    comp: dict[str, float] = {}
    try:
        for csym, cs in con.execute("SELECT symbol, composite_score FROM cross_section_factor_snapshot").fetchall():
            if cs is not None:
                comp[csym] = cs
    except Exception:
        comp = {}
    buy_q, p50 = None, None
    try:
        _trow = con.execute("SELECT buy_q, p50 FROM cross_section_thresholds ORDER BY data_cutoff DESC LIMIT 1").fetchone()
        if _trow:
            buy_q, p50 = _trow
    except Exception:
        pass
    if buy_q is None:
        buy_q = 0.22
    if p50 is None:
        p50 = 0.0

    if comp:
        hit_group, crit_group, ctrl_group = _partition_by_composite(pool[:80], comp, buy_q, p50)
        hit_group, _dropped = _harden_hit_group(hit_group)
        crit_group = _dropped + crit_group
        print(f"（四维盘前选票: buy_q={buy_q:.3f} p50={p50:.3f} → "
              f"命中{len(hit_group)} 临界{len(crit_group)} 对照{len(ctrl_group)}）")
        if _dropped:
            print("（命中组硬化: 分数达标≠命中 → 剔除 "
                  + "；".join(f"{d.get('name','?')}({d.get('reject','')})" for d in _dropped) + "）")
    else:
        # 回退：旧技术规则（均线多头+技术分），截面快照不可用时
        from trading.research.technical.market_regime import get_market_regime
        _regime = get_market_regime()
        for q in pool[:80]:
            try:
                sym_code = q["symbol"].split(".")[0]
                bars = load_bars(con, sym_code)
                if not bars or len(bars) < 30:
                    continue
                sig = technical_signal(bars, _regime)
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
        print("（截面快照不可用 → 回退旧技术分组）")
    con.close()

    # 排序：命中组按技术分降序；临界组按技术分降序；对照组按技术分降序
    hit_group.sort(key=lambda x: -x["score"])
    crit_group.sort(key=lambda x: -x["score"])
    ctrl_group.sort(key=lambda x: -x["score"])

    # 组装：命中组全部 + 临界组至多15 + 对照组补足到50（带盘后收盘价作为 price_hint）
    frozen = [{"symbol": h["symbol"], "name": h["name"], "group": "命中组",
               "price": h["price"], "frozen_score": h.get("score")} for h in hit_group]
    for c in crit_group[:15]:
        if len(frozen) >= TOP_N:
            break
        frozen.append({"symbol": c["symbol"], "name": c["name"], "group": "临界组",
                       "price": c["price"], "frozen_score": c.get("score")})
    for c in ctrl_group:
        if len(frozen) >= TOP_N:
            break
        frozen.append({"symbol": c["symbol"], "name": c["name"], "group": "对照组",
                       "price": c["price"], "frozen_score": c.get("score")})

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


def _research_one(symbol: str, name: str, price_hint: float | None = None,
                  tencent: dict | None = None) -> dict:
    """单只研究：五维+决策 → 12字段快照

    price_hint: 兜底价（快照里找不到时才用）
    tencent: 腾讯直拉实时行情（修复 2026-08-26：cron 独立进程拿不到
             实时快照服务的内存快照，价格改为腾讯批量接口直拉）
    新增：后续表现字段（信号后价格及相对基准表现）
    """
    from scripts.intraday_pipeline import IntradayPipeline
    pipe = IntradayPipeline()
    # 1) 优先腾讯直拉实时价（一次性批量拉取，见 main()）
    if tencent:
        realtime = {
            "symbol": symbol, "name": name,
            "price": tencent.get("price") or price_hint or 0,
            "pct_chg": tencent.get("pct_chg", 0),
            "volume_ratio": tencent.get("volume_ratio", 0),
            "turnover": tencent.get("turnover", 0),
            "amount_wan": tencent.get("amount_wan", 0),
            "high": tencent.get("high", 0), "low": tencent.get("low", 0),
            "open": tencent.get("open", 0), "prev_close": tencent.get("prev_close", 0),
        }
    else:
        # 2) 回退：实时快照服务（Router 进程内存有数据时才有）
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
        "后续表现": {"信号价": r.get("price"), "当前价": r.get("price"), "涨跌幅": 0},
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
    # 更新后续表现
    if prev.get("参考价格") and cur.get("参考价格"):
        ref_price = prev["参考价格"]
        cur_price = cur["参考价格"]
        if ref_price > 0:
            pct = (cur_price - ref_price) / ref_price * 100
            cur["后续表现"] = {
                "信号价": ref_price,
                "当前价": cur_price,
                "涨跌幅": round(pct, 2),
                "表现": "盈利" if pct > 0 else ("亏损" if pct < 0 else "持平")
            }
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

    # 逐只研究（先腾讯批量直拉实时价，一次请求，修复 2026-08-26）
    tencent_map = _tencent_prices([f["symbol"] for f in frozen])
    results = []
    for f in frozen:
        try:
            r = _research_one(f["symbol"], f["name"], price_hint=f.get("price"),
                              tencent=tencent_map.get(f["symbol"]))
            r["观察组"] = f.get("group", "未分组")
            # 修复(2026-09-16 改进3)：记录当日真实涨跌幅(腾讯pct_chg)。
            # 观察池"后续表现"原来以 v1 旧冻结参考价为基准算累计涨幅，
            # 冻结价可能是多日前旧值 → 单日却显示 +33.8% 失真。这里单独
            # 记当日真实涨跌，统计时优先用它，避免失真基准污染分组收益率。
            q = tencent_map.get(f["symbol"]) or {}
            day_chg = q.get("pct_chg")
            # 2026-09-29 第二批S2修复：缺失时记 None 并在分组统计中剔除，
            # 原默认填 0.0 会把"取不到行情"伪装成"平盘"，污染分组收益。
            r["当日涨跌"] = day_chg
            results.append(r)
        except Exception as exc:
            results.append({"symbol": f["symbol"], "name": f["name"],
                            "status": "ERROR", "error": str(exc)[:50]})
        time.sleep(0.1)

    # 后续表现基准（修复 2026-08-26）：以 v1 冻结时点为基准，计算相对
    # 冻结日的累计涨跌幅。原实现逐级对比上一时点，最终只反映"相对上一
    # 时点"（close→review 价格相同 → 收益率恒为 0），失去验证意义。
    tp_order = ["v1", "v2", "v3", "c10", "c1030", "am", "pm", "pm14", "pm1430",
                "vfinal", "close", "review"]
    base_map = None
    if tp != "v1":
        base_path = _snapshot_path(now, "v1")
        if base_path.exists():
            base_data = json.loads(base_path.read_text(encoding="utf-8"))
            base_map = {r["symbol"]: r for r in base_data.get("results", [])}
    if base_map:
        for r in results:
            b = base_map.get(r["symbol"])
            if b and b.get("参考价格"):
                ref_price = b["参考价格"]
                cur_price = r.get("参考价格")
                if ref_price > 0 and cur_price:
                    pct = (cur_price - ref_price) / ref_price * 100
                    r["后续表现"] = {
                        "信号价": ref_price,
                        "当前价": cur_price,
                        "涨跌幅": round(pct, 2),
                        "表现": "盈利" if pct > 0 else ("亏损" if pct < 0 else "持平")
                    }
    else:
        # 无 v1 基准时，退化为对比上一时点
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
    # 优化 2026-10-08：快照内附中性基准 + 策略组，供后续多期验证聚合
    _bench = _market_benchmark()
    from scripts.sim_validation import is_buy_action as _is_buy  # type: ignore
    _strategy_syms = [r["symbol"] for r in results
                      if r.get("status") == "OK" and _is_buy(r.get("当前动作"), r.get("总评分"), r.get("VETO状态"))]
    snap_data = {"timepoint": tp, "timepoint_zh": TIMEPOINT_ZH[tp],
                 "generated_at": now.isoformat(), "results": results,
                 "benchmark": _bench, "strategy_group": _strategy_syms,
                 "sector_context": _read_sector_context()}
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

    # === 分组收益率对比（优化 2026-10-08）===
    # 优化1 中性基准：命中组/策略组与"同日大盘"比较，而非只跟被刻意挑弱的对照组比；
    # 优化2 策略组验证臂：五维综合分≥建仓线 且 动作∈建仓类 且 无VETO（检验真实策略，非纯技术）；
    # 优化3 口径对称：命中组的综合分门槛另列"综合分≥0.3"口径，与全体命中组并排展示。
    print("\n=== 分组收益率对比（含中性基准+策略组）===")
    try:
        from scripts.sim_validation import is_buy_action as _is_buy, BUY_MIN  # noqa
    except Exception:
        def _is_buy(a, s, v):
            return (v in (None, "", "NO_VETO")
                    and a in {"强势研究候选", "建仓", "加仓", "强烈建仓", "增持", "买入", "买入建仓"}
                    and s is not None and s >= 0.20)
    HIT_SCORE_MIN = 0.3  # 命中组"有效"综合分门槛

    def _ret(r):
        d = r.get("当日涨跌")
        if d is not None:
            return d
        perf = r.get("后续表现") or {}
        return perf.get("涨跌幅")

    def _avg(rs):
        vals = [x for x in (_ret(r) for r in rs) if x is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    strategy = [r for r in ok if _is_buy(r.get("当前动作"), r.get("总评分"), r.get("VETO状态"))]
    arms = [
        ("命中组(全体)", [r for r in ok if r.get("观察组") == "命中组"]),
        ("命中组(综合分≥0.3)", [r for r in ok if r.get("观察组") == "命中组"
                              and r.get("总评分") is not None and r.get("总评分") >= HIT_SCORE_MIN]),
        ("临界组", [r for r in ok if r.get("观察组") == "临界组"]),
        ("对照组", [r for r in ok if r.get("观察组") == "对照组"]),
        ("策略组(五维建仓)", strategy),
    ]
    arm_avg: dict[str, float | None] = {}
    for label, rs in arms:
        m, n = _avg(rs)
        arm_avg[label] = m
        print(f"  {label}: " + (f"平均{m:+.2f}% | 盈{sum(1 for x in (_ret(r) for r in rs) if x is not None and x > 0)}只 亏{sum(1 for x in (_ret(r) for r in rs) if x is not None and x < 0)}只 | {n}只" if m is not None else "无有效数据"))

    bench = _bench if isinstance(_bench, dict) else {}
    bench_main = bench.get("中证全指", bench.get("沪深300"))
    if bench:
        parts = " ".join(f"{k}{v:+.2f}%" for k, v in bench.items() if v is not None)
        print(f"  ── 中性基准(同日): {parts}")
    if bench_main is not None:
        for label in ("命中组(全体)", "命中组(综合分≥0.3)", "策略组(五维建仓)"):
            if arm_avg.get(label) is not None:
                print(f"  → {label} vs 中证全指: {arm_avg[label] - bench_main:+.2f}%")
    if arm_avg.get("命中组(全体)") is not None and arm_avg.get("对照组") is not None:
        print(f"  → 命中组(全体) vs 对照组: {arm_avg['命中组(全体)'] - arm_avg['对照组']:+.2f}%")


    print(f"\n快照已存: {snap_path}")


if __name__ == "__main__":
    main()
