# ruff: noqa: E402
# Standalone CLI bootstrap configures paths or runtime before project imports.
# -*- coding: utf-8 -*-
"""
个股标准交叉验证包（正式工具，2026-09-07 固化自 tmp_xcheck_600354_600479.py）
用法: .venv/Scripts/python.exe scripts/cross_validate.py 600354.SH [600479.SH ...]
对每只输出5项（对应 yiyi-investment-assistant 交叉验证要求）:
  1. 近10日K线表(收盘/涨跌/成交额) + MA20/60 位置
  2. 龙虎榜近10日逐日 + 区间净额(净卖<=-1亿=硬否决红灯)
  3. 主力资金5/10/20日净额(moneyflow 特大单+大单, 万元->亿) + 净流出天数
  4. 矛盾信号判断: 四维说建仓但主力连流出/暴涨暴跌 -> 出货警示
  5. 结论提示
"""

import sys
import os
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ["NO_PROXY"] = "*"
import tushare as ts
import tushare.pro.client as _tc

_orig = _tc.DataApi.__init__


def _patched(self, *a, **kw):
    _orig(self, *a, **kw)
    self._DataApi__http_url = "https://api.tushare.pro/dataapi"


_tc.DataApi.__init__ = _patched


def _ts_token() -> str:
    """2026-09-28 S2修复：token改读settings/.env，不再硬编码。"""
    import os
    import sys

    tok = os.environ.get("TUSHARE_TOKEN", "")
    if not tok:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from config.settings import settings

            tok = (settings.tushare_token or "").strip()
        except Exception:
            tok = ""
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN 未配置（环境变量或 .env）")
    return tok


pro = ts.pro_api(_ts_token())


def retry(fn, n=4):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print("retry", i, str(e)[:60], flush=True)
            time.sleep(3 * (i + 1))
    raise RuntimeError("fail")


def trade_days(start, end):
    """取交易日列表。Tushare 的 is_open 过滤参数改为整数 1；且接口偶发返回空 df，
    必须重试到非空，否则下游 KeyError。"""

    def _fetch():
        for is_open in (1, "1"):
            try:
                df = pro.trade_cal(exchange="SSE", is_open=is_open, start_date=start, end_date=end)
            except Exception:
                df = None
            if df is not None and len(df) and "is_open" in df.columns:
                return df
        # 兜底：不带 is_open 取全量再本地过滤
        df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
        if df is not None and len(df) and "is_open" in df.columns:
            return df[df["is_open"] == 1]
        return df

    df = retry(_fetch)
    return sorted(df[df["is_open"] == 1]["cal_date"].tolist())


def xcheck(code):
    name = ""
    try:
        sb = retry(lambda: pro.stock_basic(ts_code=code, fields="ts_code,name"))
        name = sb["name"].iloc[0] if len(sb) else ""
    except Exception:
        pass
    print("=" * 50)
    print(f"【{name} {code}】")
    tds_all = trade_days("20260601", time.strftime("%Y%m%d"))
    end = tds_all[-1]
    d = retry(lambda: pro.daily(ts_code=code, start_date="20260825", end_date=end))
    d = d.sort_values("trade_date")
    print("\n--近10日K线--")
    max_chg = 0
    for _, r in d.iterrows():
        max_chg = max(max_chg, abs(r["pct_chg"]))
        print(
            f"  {r['trade_date']}  收{r['close']:.2f}  涨{r['pct_chg']:+.2f}%  额{r['amount'] / 100000:.2f}亿"
        )
    bars = retry(lambda: pro.daily(ts_code=code, start_date="20260501", end_date=end))
    bars = bars.sort_values("trade_date")
    closes = bars["close"].tolist()
    price = closes[-1]
    if len(closes) >= 60:
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        print(
            f"  现价{price:.2f} MA20={ma20:.2f} MA60={ma60:.2f} 乖离20日={(price / ma20 - 1) * 100:+.1f}%"
        )

    tds = trade_days("20260824", end)[-10:]
    net_sum = 0
    hit = 0
    print("\n--龙虎榜近10日--")
    for td in tds:
        try:
            tl = pro.top_list(trade_date=td)
            sub = tl[tl["ts_code"] == code]
            for _, r in sub.iterrows():
                net = r.get("net_amount", 0) or 0
                net_sum += net
                hit += 1
                print(f"  {td} 上榜 净额{net / 10000:.2f}万")
        except Exception as e:
            print(f"  {td} err {str(e)[:40]}")
        time.sleep(0.12)
    net_yi = net_sum / 1e8
    print(f"  龙虎榜区间净额合计: {net_sum / 10000:.2f}万 ({net_yi:.2f}亿), 上榜{hit}次")
    red = net_yi <= -1.0

    try:
        mf = retry(lambda: pro.moneyflow(ts_code=code, start_date="20260601", end_date=end))
        mf = mf.sort_values("trade_date")
        mf["big_net"] = (mf["buy_elg_amount"] - mf["sell_elg_amount"]) + (
            mf["buy_lg_amount"] - mf["sell_lg_amount"]
        )
        print("\n--主力资金(moneyflow 特大单+大单, 万元->亿)--")
        m5 = mf["big_net"].tail(5).sum() / 10000
        m10 = mf["big_net"].tail(10).sum() / 10000
        m20 = mf["big_net"].tail(20).sum() / 10000
        neg = int((mf["big_net"] < 0).tail(10).sum())
        print(f"  5日净额: {m5:+.2f}亿 | 10日净额: {m10:+.2f}亿 | 20日净额: {m20:+.2f}亿")
        print(f"  近10日中净流出天数: {neg}/10")
    except Exception as e:
        m5 = m10 = m20 = 0
        neg = 0
        print("  moneyflow err", str(e)[:60])

    # 矛盾信号判断
    warns = []
    if red:
        warns.append(f"🔴 龙虎榜硬否决: 区间净卖{net_yi:.2f}亿 <= -1亿")
    if m10 <= -1.0:
        warns.append(f"🟠 主力资金10日净流出{m10:.2f}亿, 近10日{neg}天流出(边拉边卖/散户接力)")
    if max_chg >= 9.5:
        warns.append("🟠 近10日出现±9.5%以上剧烈波动(妖股/出货嫌疑)")
    if len(closes) >= 20 and price / sum(closes[-20:]) * 20 - 1 > 0.20:
        warns.append("🟠 乖离20日线>20%(超买)")
    print("\n--矛盾信号--")
    if warns:
        for w in warns:
            print("  " + w)
    else:
        print("  未见明显出货/超买信号")
    print(
        "结论: "
        + (
            "❌ 不推荐追高"
            if red
            else (
                "⚠️ 需谨慎/等资金面确认"
                if (m10 <= -1.0 or max_chg >= 9.5)
                else "✅ 技术形态与资金面未见冲突"
            )
        )
    )


if __name__ == "__main__":
    codes = sys.argv[1:] or ["600354.SH"]
    for c in codes:
        xcheck(c)
    print("=" * 50)
    print("DONE")
