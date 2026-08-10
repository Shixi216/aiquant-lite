"""腾讯全市场快照 Provider（替代被风控的东财 spot 接口）

使用 qt.gtimg.cn 行情接口批量拉取全市场快照。
数据字段：代码/名称/最新价/涨跌幅/成交量/成交额等。
"""
from __future__ import annotations

from database.db import open_database

import time
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

TZ = timezone(timedelta(hours=8))

# 腾讯批量行情接口：每次最多60只
QT_URL = "https://qt.gtimg.cn/q={codes}"
# 市场前缀：6开头→sh，其余→sz（含0/3开头深市、688科创板sh、8/4开头北交所bj）
def _market_prefix(code: str) -> str:
    if code.startswith(("6", "9", "688")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


class TencentSnapshotProvider:
    provider_name = "Tencent"

    def fetch_market_snapshot(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """拉取全市场快照（分批60只，间隔1秒防风控）"""
        if symbols is None:
            # 从股票池读取全部代码
            import duckdb
            from config.settings import settings
            from pathlib import Path
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[2] / db_path
            con = open_database(str(db_path), read_only=True)
            rows = con.execute(
                "SELECT symbol FROM stock_universe WHERE active=true LIMIT 10000"
            ).fetchall()
            con.close()
            symbols = [r[0].split(".")[0] for r in rows if r[0]]

        # 分批（每批80只，腾讯接口上限约80），4线程并发加速
        frames = []
        batch_size = 80
        batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(self._fetch_batch, ",".join(f"{_market_prefix(c)}{c}" for c in batch)): i
                for i, batch in enumerate(batches)
            }
            results = {}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    df = fut.result()
                    if df is not None and not df.empty:
                        results[idx] = df
                except Exception:
                    pass
                time.sleep(0.15)  # 轻量限速

        for idx in sorted(results):
            frames.append(results[idx])
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames, ignore_index=True)
        return result

    def _fetch_batch(self, codes: str) -> pd.DataFrame | None:
        import urllib.request

        url = QT_URL.format(codes=codes)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://gu.qq.com/",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            text = resp.read().decode("gbk", errors="ignore")
        except Exception:
            return None

        rows = []
        for line in text.split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            try:
                key, val = line.split("=", 1)
                code = key.split("_")[-1]
                fields = val.strip('"').split("~")
                if len(fields) < 45:
                    continue
                rows.append({
                    "symbol": f"{fields[2]}.{'SH' if code.startswith('sh') else 'SZ' if code.startswith('sz') else 'BJ'}",
                    "code": fields[2],
                    "name": fields[1],
                    "close": float(fields[3]) if fields[3] else None,
                    "pct_chg": float(fields[32]) if fields[32] else None,
                    "volume": float(fields[6]) if fields[6] else 0,      # 手
                    "amount": float(fields[37]) if fields[37] else 0,    # 万元
                    "high": float(fields[33]) if fields[33] else None,
                    "low": float(fields[34]) if fields[34] else None,
                    "open": float(fields[5]) if fields[5] else None,
                    "turnover_rate": float(fields[38]) if len(fields) > 38 and fields[38] else None,
                    "snapshot_time": datetime.now(tz=TZ),
                })
            except Exception:
                continue
        if not rows:
            return None
        return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    start = time.perf_counter()
    p = TencentSnapshotProvider()
    # 小规模测试：20只
    test = ["000100", "600010", "600509", "600664", "002407", "601991", "600172",
            "002185", "002440", "002141", "603980", "000848", "300138", "002889",
            "000566", "002687", "300214", "600419", "002860", "002279"]
    df = p.fetch_market_snapshot(test)
    print(f"测试 {len(test)} 只 → 返回 {len(df)} 只, 耗时 {time.perf_counter()-start:.1f}s")
    for _, r in df.head(8).iterrows():
        print(f"  {r['code']} {r['name']} 收{r['close']:.2f} 涨{r['pct_chg']:+.2f}%")
