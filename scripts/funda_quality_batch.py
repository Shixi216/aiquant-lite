# ruff: noqa: E402
# Standalone CLI bootstrap configures paths or runtime before project imports.
# -*- coding: utf-8 -*-
"""
全市场基本面质量批量核验（2026-09-28 固化工具，token 额度专项）
- 对主板非ST活跃票批量拉 Tushare fina_indicator + income + balancesheet
- 计算质量四项: 扣非/归母比、净利同比、应收/营收、ROE + 亏损/大降标记
- 落库 funda_quality_screen 表
- 用法: python scripts/funda_quality_batch.py [--limit N] [--max-age-days 30]
幂等: 表存在则按 end_date+报告期增量更新, 7天内跑过的跳过

⚠️ 运行约束（2026-09-29 第二批S3修正文档，与实现如实对齐）：
  本脚本用裸 duckdb.connect 直连主库。DuckDB 单写者模型下，
  必须在 Router/快照刷新均未持库时运行（离线窗口），否则报文件锁冲突。
  不设置 HERMES_DB_MAINTENANCE（那是 database.db 守卫用的，本脚本
  不经过该层）。推荐：先确认 8765 健康检查显示刷新空闲、无
  snapshot_refresh.lock，再跑；或停 Router 后跑完再由看门狗拉起。
"""

import sys
import os
import time

sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime

import tushare as ts
import duckdb

TS_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
DB = "database/hermes_opc.duckdb"
TABLE = "funda_quality_screen"
REPORT_PERIOD = "20260630"  # 中报口径


def _safe_float(v):
    """2026-09-29 修复：income/balancesheet 里 pandas 会把缺失值给成
    None 而非 NaN（见601998实测），原 float() 直接 TypeError，
    导致100只大银行/金融股核验失败。统一安全转换。"""
    if v is None:
        return None
    try:
        s = str(v)
        if s in ("nan", "None", "NaT", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    # 2026-09-29 第二批L5修复：--limit 越界/非数字参数给明确报错而非 IndexError/ValueError 崩栈
    limit = 0
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("用法: --limit N（N为正整数）")
            sys.exit(2)
    pro = ts.pro_api(TS_TOKEN)

    con = duckdb.connect(DB)
    con.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        symbol VARCHAR PRIMARY KEY,
        short_name VARCHAR,
        end_date VARCHAR,
        revenue DOUBLE, n_income DOUBLE, profit_dedt DOUBLE,
        roe DOUBLE, debt_to_assets DOUBLE,
        netprofit_yoy DOUBLE, or_yoy DOUBLE,
        accounts_receiv DOUBLE,
        ratio_dedt DOUBLE, ratio_ar DOUBLE,
        flags VARCHAR,
        updated_at TIMESTAMP
    )""")

    # 股票池: 主板非ST在市
    _pool = (
        pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,list_board,is_st")
        if False
        else None
    )
    # stock_basic 无 list_board 字段, 用 ts_code 前缀过滤
    basics = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
    df = basics[~basics["ts_code"].str[:3].isin(["300", "301", "688", "698"])]
    df = df[~df["ts_code"].str[:2].isin(["92", "43", "83", "87", "88", "4"])]  # 北交所
    df = df[~df["name"].str.contains("ST|退", na=False)]
    df["symbol"] = df["ts_code"]
    # 排除 ST 用名字判断 (Tushare stock_basic 无 is_st)
    codes = df["ts_code"].tolist()
    names = dict(zip(df["ts_code"], df["name"]))
    print(f"股票池: {len(codes)} 只 (主板非ST)")

    # 已更新的跳过 (7天内)
    done = set()
    try:
        rows = con.execute(
            f"SELECT symbol FROM {TABLE} WHERE updated_at > now() - INTERVAL 7 DAY AND end_date='{REPORT_PERIOD}'"
        ).fetchall()
        done = {r[0] for r in rows}
    except Exception:
        pass
    todo = [c for c in codes if c not in done]
    # 排除历史误写的 NO_REPORT (窗口bug遗留), 强制重查
    try:
        bad = con.execute(f"SELECT symbol FROM {TABLE} WHERE flags='NO_REPORT'").fetchall()
        badset = {r[0] for r in bad}
        todo = [c for c in codes if c not in done or c in badset]
    except Exception:
        pass
    if limit:
        todo = todo[:limit]
    print(f"待核验: {len(todo)} 只 (已更新 {len(done)})")

    ok = 0
    fail = 0
    blacklist = 0
    t0 = time.time()
    for i, code in enumerate(todo):
        try:
            fi = pro.fina_indicator(
                ts_code=code,
                start_date="20260101",
                end_date="20260928",
                fields="ts_code,end_date,profit_dedt,roe,debt_to_assets,netprofit_yoy,or_yoy",
            )
            fi = fi.drop_duplicates(subset=["end_date"])
            fi = fi[fi["end_date"] == REPORT_PERIOD]
            if fi.empty:
                # 无中报数据(次新/未披露), 记为 NO_REPORT
                con.execute(
                    f"INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        code,
                        names.get(code, ""),
                        REPORT_PERIOD,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "NO_REPORT",
                        datetime.now(),
                    ],
                )
                ok += 1
            else:
                r = fi.iloc[0]
                inc = pro.income(
                    ts_code=code,
                    start_date="20260101",
                    end_date="20260928",
                    fields="ts_code,end_date,revenue,n_income",
                )
                inc = inc.drop_duplicates(subset=["end_date"])
                inc = inc[inc["end_date"] == REPORT_PERIOD]
                revenue = _safe_float(inc.iloc[0]["revenue"]) if not inc.empty else None
                n_inc = _safe_float(inc.iloc[0]["n_income"]) if not inc.empty else None
                bs = pro.balancesheet(
                    ts_code=code,
                    start_date="20260101",
                    end_date="20260928",
                    fields="ts_code,end_date,accounts_receiv",
                )
                bs = bs.drop_duplicates(subset=["end_date"])
                bs = bs[bs["end_date"] == REPORT_PERIOD]
                ar = _safe_float(bs.iloc[0]["accounts_receiv"]) if not bs.empty else None

                dedt = (
                    float(r["profit_dedt"])
                    if r["profit_dedt"] is not None and str(r["profit_dedt"]) != "nan"
                    else None
                )
                yoy = (
                    float(r["netprofit_yoy"])
                    if r["netprofit_yoy"] is not None and str(r["netprofit_yoy"]) != "nan"
                    else None
                )
                roe = float(r["roe"]) if r["roe"] is not None and str(r["roe"]) != "nan" else None

                flags = []
                if n_inc is not None and n_inc < 0:
                    flags.append("FUNDAMENTAL_LOSS")
                if yoy is not None and yoy < -30:
                    flags.append("NET_PROFIT_DOWN")
                ratio_dedt = (
                    (dedt / n_inc) if (dedt is not None and n_inc not in (None, 0)) else None
                )
                if ratio_dedt is not None and n_inc and n_inc > 0 and ratio_dedt > 2.0:
                    flags.append("NON_RECURRING_DOMINANT")  # 扣非远大于归母=非经常损失拖累
                # 2026-09-29 第二批L6修复：扣非为负但归母为正 = 利润靠非经常
                # 收益撑着（政府补助/卖资产等），原逻辑只查 ratio>2 一个方向，
                # 这类"盈新式"粉饰票永远漏放行。n_inc==0 时 ratio_dedt 为 None
                # 是除零保护，正确。
                if dedt is not None and dedt < 0 and n_inc is not None and n_inc > 0:
                    flags.append("NON_RECURRING_DOMINANT")
                ratio_ar = (ar / revenue) if (ar is not None and revenue) else None
                if ratio_ar is not None and ratio_ar > 2.5:
                    flags.append("AR_HEAVY")
                if (
                    roe is not None
                    and roe < 1.0
                    and (n_inc is None or (n_inc is not None and n_inc > 0))
                ):
                    flags.append("LOW_ROE")
                fl = ",".join(flags)
                if any(
                    f in fl
                    for f in (
                        "FUNDAMENTAL_LOSS",
                        "NET_PROFIT_DOWN",
                        "AR_HEAVY",
                        "NON_RECURRING_DOMINANT",
                    )
                ):
                    blacklist += 1

                def _f(x):
                    try:
                        return float(x) if x is not None and str(x) != "nan" else None
                    except (TypeError, ValueError):
                        return None

                con.execute(
                    f"INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        code,
                        names.get(code, ""),
                        REPORT_PERIOD,
                        revenue,
                        n_inc,
                        dedt,
                        roe,
                        _f(r.get("debt_to_assets")),
                        yoy,
                        _f(r.get("or_yoy")),
                        ar,
                        ratio_dedt,
                        ratio_ar,
                        fl,
                        datetime.now(),
                    ],
                )
                ok += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  ✗ {code}: {e}")
        if (i + 1) % 200 == 0:
            el = time.time() - t0
            print(
                f"  进度 {i + 1}/{len(todo)} | 成功{ok} 失败{fail} 黑名单{blacklist} | {el:.0f}s ({(i + 1) / el:.1f}只/秒)"
            )
    print(f"完成: 成功{ok} 失败{fail} 黑名单标记{blacklist} | 总耗时 {time.time() - t0:.0f}s")

    # 汇总
    print("\n=== 汇总 ===")
    rows = con.execute(
        f"SELECT flags, COUNT(*) FROM {TABLE} WHERE end_date='{REPORT_PERIOD}' GROUP BY flags ORDER BY 2 DESC LIMIT 12"
    ).fetchall()
    for f, n in rows:
        print(f"  {f or '(干净)'}: {n}")
    con.close()


if __name__ == "__main__":
    main()
