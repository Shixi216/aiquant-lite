"""One bounded provider call in a disposable process; invoked by the collector."""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")


def safe_error(exc: BaseException) -> str:
    text = re.sub(r"(?i)(token|sdk-key|authorization|password)=([^\s&]+)", r"\1=<redacted>", str(exc))
    return re.sub(r"//[^/@\s]+:[^/@\s]+@", "//<redacted>@", text)[:300]


def install_tolerant_board_fetch() -> None:
    """东财板块榜分页容错（2026-10-18）。

    原实现（akshare fetch_paginated_data）：pz=100 按代码排序翻 6 页，每页失败重试 3 次
    → 单次采集可发出 18 个请求，东财 push2 对高频请求直接断连（RemoteDisconnected），
    导致概念榜整体不可用、快照降级、cron 记为失败。

    本实现：
    1. pz=200，页数减半；
    2. 排序改为按涨跌幅(f3)降序，首页即最强板块——分页中断时已取部分仍可用于 Top 排名；
    3. 去掉重试（重试只会加重限流），任一页失败立即停止翻页并保留已取数据；
    4. 首页失败才抛错，由上层标记该源不可用。
    """
    import math
    import random

    import pandas as pd
    import requests as _requests

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"}

    def fetch_paginated_data(url: str, base_params: dict, timeout: int = 15):
        params = dict(base_params)
        params.update({"pz": "200", "fid": "f3", "po": "1"})
        frames = []
        partial = False
        total_page = 1
        max_pages = 8                                  # 硬上限，防止页数计算异常导致无限翻页
        deadline = time.monotonic() + 25               # 硬预算，保证不撞上层 35s 单源超时
        page = 1
        while page <= max_pages:
            if page > 1:
                # 页间隔 1.5-2.5s：既避开东财瞬时限流，又保证在预算内翻完
                time.sleep(random.uniform(1.5, 2.5))
            if time.monotonic() > deadline:
                partial = True
                break
            try:
                response = _requests.get(url, params=dict(params, pn=str(page)),
                                         timeout=timeout, headers=headers)
                response.raise_for_status()
                data = response.json().get("data") or {}
            except Exception:
                if page == 1:
                    raise
                partial = True
                break
            diff = data.get("diff") or []
            if not diff:
                break
            frames.append(pd.DataFrame(diff))
            if page == 1:
                total = data.get("total") or len(diff)
                total_page = max(1, math.ceil(total / max(len(diff), 1)))
            if page >= total_page:
                break
            if page >= max_pages:
                partial = True                          # 还有页没取完就被上限截断
            page += 1
        df = pd.concat(frames, ignore_index=True)
        df["f3"] = pd.to_numeric(df["f3"], errors="coerce")
        df.sort_values(by="f3", ascending=False, inplace=True, ignore_index=True)
        df.insert(0, "index", df.index + 1)
        df.attrs["partial"] = partial
        df.attrs["pages_fetched"] = len(frames)
        setattr(fetch_paginated_data, "last_partial", partial)
        return df

    import akshare.stock.stock_board_concept_em as _concept_module
    import akshare.stock.stock_board_industry_em as _industry_module
    _concept_module.fetch_paginated_data = fetch_paginated_data
    _industry_module.fetch_paginated_data = fetch_paginated_data


def fetch(name: str, day: str, router_url: str, board: str = "", symbol: str = "") -> dict:
    import requests

    observed = []
    original = requests.sessions.Session.request

    def bounded(session, method, url, **kwargs):
        kwargs["timeout"] = (4, 8)
        response = original(session, method, url, **kwargs)
        response.raise_for_status()
        parts = urlsplit(url)
        if parts.hostname and parts.hostname.endswith("eastmoney.com"):
            try:
                value = response.json()
                observed.append({"endpoint": parts.hostname + parts.path, "response": value})
            except ValueError:
                pass
        return response

    requests.sessions.Session.request = bounded
    extra = {}
    if name == "ts_calendar":
        # Tushare 交易日历兜底：新浪日历（akshare）不可达时仍能校验交易日，
        # 否则整个采集会因拿不到日历直接失败。
        root = Path(__file__).resolve().parents[1]
        token = ""
        env_file = root / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("TUSHARE_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not token:
            raise RuntimeError("TUSHARE_TOKEN missing for calendar fallback")
        import tushare as ts
        import tushare.pro.client as _tushare_client
        _original_init = _tushare_client.DataApi.__init__

        def _patched_init(self, *args, **kwargs):
            _original_init(self, *args, **kwargs)
            self._DataApi__http_url = "https://api.tushare.pro/dataapi"

        _tushare_client.DataApi.__init__ = _patched_init
        pro = ts.pro_api(token)
        start = (datetime.strptime(day, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
        end = (datetime.strptime(day, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")
        frame = pro.trade_cal(exchange="SSE", start_date=start, end_date=end,
                              fields="cal_date,is_open")
        rows = [{"trade_date": str(r.cal_date)[:4] + "-" + str(r.cal_date)[4:6] + "-"
                 + str(r.cal_date)[6:8]} for r in frame.itertuples() if int(r.is_open) == 1]
        value = {"columns": ["trade_date"], "rows": rows}
        extra = {"fallback_for": "ak_calendar", "range": [start, end]}
    elif name == "tencent_quotes":
        from concurrent.futures import ThreadPoolExecutor

        items = []
        offset = 0
        while True:
            value = requests.get(router_url.rstrip("/") + "/v1/universe", params={
                "active_only": "true", "offset": offset, "limit": 4000}).json()
            items.extend(value["items"])
            offset += len(value["items"])
            if offset >= value["total"]:
                break
            if not value["items"]:
                raise RuntimeError("universe pagination incomplete")
        symbols = sorted({x["symbol"] for x in items})

        def batch(batch_symbols):
            codes = [s.rsplit(".", 1)[1].lower() + s.split(".")[0] for s in batch_symbols]
            raw = requests.get("https://qt.gtimg.cn/q=" + ",".join(codes),
                               headers={"User-Agent": "Mozilla/5.0"}).content.decode("gbk")
            rows = []
            for line in raw.split(";"):
                if "~" not in line:
                    continue
                key, text = line.split("=", 1)
                f = text.strip().strip('"').split("~")
                code = key.strip().split("_")[-1]
                if len(f) < 39 or not f[30]:
                    continue
                rows.append({"symbol": f[2] + "." + code[:2].upper(), "name": f[1],
                             "price": float(f[3]), "pct_chg": float(f[32]),
                             "amount": float(f[37]) * 10000,
                             "source_timestamp": f[30], "trade_date": f[30][:8]})
            return rows

        batches = [symbols[i:i + 80] for i in range(0, len(symbols), 80)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = [row for group in pool.map(batch, batches) for row in group]
        extra = {"expected_count": len(symbols), "request_count": len(batches),
                 "universe_version": value["universe_version"]}
        value = {"rows": rows, "columns": list(rows[0]) if rows else []}
    elif name == "router_quotes":
        response = requests.get(router_url.rstrip("/") + "/v1/market-snapshots/latest",
                                params={"limit": 5000, "maximum_age_seconds": 86400})
        value = response.json()
    elif name.startswith("ak_"):
        import akshare as ak

        if name in {"ak_concepts", "ak_industries"}:
            install_tolerant_board_fetch()

        functions = {
            "ak_concepts": lambda: ak.stock_board_concept_name_em(),
            "ak_industries": lambda: ak.stock_board_industry_name_em(),
            "ak_concept_flow": lambda: ak.stock_sector_fund_flow_rank(
                indicator="今日", sector_type="概念资金流"),
            "ak_limit_up": lambda: ak.stock_zt_pool_em(date=day),
            "ak_limit_down": lambda: ak.stock_zt_pool_dtgc_em(date=day),
            "ak_broken": lambda: ak.stock_zt_pool_zbgc_em(date=day),
            "ak_previous_pool": lambda: ak.stock_zt_pool_previous_em(date=day),
            "ak_lhb": lambda: ak.stock_lhb_detail_em(start_date=day, end_date=day),
            "ak_calendar": lambda: ak.tool_trade_date_hist_sina(),
            "ak_concept_members": lambda: ak.stock_board_concept_cons_em(symbol=board),
            "ak_lhb_buy": lambda: ak.stock_lhb_stock_detail_em(symbol=symbol, date=day, flag="买入"),
            "ak_lhb_sell": lambda: ak.stock_lhb_stock_detail_em(symbol=symbol, date=day, flag="卖出"),
        }
        frame = functions[name]()
        value = {"columns": list(frame.columns), "rows": json.loads(frame.to_json(
            orient="records", date_format="iso", force_ascii=False))}
        if name in {"ak_concepts", "ak_industries"}:
            value["partial"] = bool(frame.attrs.get("partial"))
            value["pages_fetched"] = frame.attrs.get("pages_fetched")
    elif name.startswith("zz_"):
        from zzshare.client import DataApi

        class OnceApi(DataApi):
            def _request_with_retry(self, url, params=None, max_retries=0):
                return requests.get(url, params=params, headers=self.headers, timeout=(4, 8))

        # Explicit anonymous key: do not inherit credentials from the main application.
        api = OnceApi(token="anonymous", timeout=8)
        functions = {
            "zz_sentiment": lambda: api.market_sentiment(date1=day, date2=day),
            "zz_hot": lambda: api.uplimit_hot(date1=day),
            "zz_limit_up": lambda: api.uplimit_stocks(date1=day),
            "zz_lhb": lambda: api.lhb_list(date1=day),
            "zz_lhb_detail": lambda: api.lhb_detail(date1=day, stock_code=symbol),
        }
        value = functions[name]()
    else:
        raise ValueError("unknown provider")
    if value is None:
        raise RuntimeError("provider returned None, not a valid zero observation")
    count = len(value.get("rows", [])) if name.startswith("ak_") or name == "tencent_quotes" else len(value)
    if name == "zz_hot":
        count = len(value.get("stock_info", {}))
    return {"status": "OK" if count else "EMPTY", "row_count": count,
            "payload": value, "provider_responses": observed, **extra}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--router-url", default="http://127.0.0.1:8765")
    parser.add_argument("--board", default="")
    parser.add_argument("--symbol", default="")
    args = parser.parse_args()
    started = time.monotonic()
    result = {"name": args.name, "requested_trade_date": args.date,
              "started_at": datetime.now(TZ).isoformat()}
    try:
        result.update(fetch(args.name, args.date, args.router_url, args.board, args.symbol))
    except Exception as exc:
        result.update(status="ERROR", error=type(exc).__name__ + ": " + safe_error(exc))
    result.update(available_at=datetime.now(TZ).isoformat(),
                  elapsed_seconds=round(time.monotonic() - started, 3))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
                           encoding="utf-8")


if __name__ == "__main__":
    main()
