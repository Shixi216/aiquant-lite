"""双数据源（Dual-Source）行情获取层。

- OfficialTushareProvider：官方 Tushare SDK（环境变量 TUSHARE_OFFICIAL_TOKEN，
  缺省回退项目已有 TUSHARE_TOKEN），主力源，负责基础行情。
- ProxyDataProvider：Tushare 兼容 HTTP 代理（WEALTH_LAB_TS_URL/TOKEN），
  高阶/备用源（如官方积分不足的 fund_adj）。
- fallback 编排：优先官方；官方因积分/限频/网络报错时自动降级代理。
  行级来源审计：返回帧携带 data_source（OFFICIAL / PROXY）。

token 只在请求中出现，错误信息一律脱敏（不回显 token 与完整 URL）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pandas as pd

from trading.wealth_lab.config import (
    PROJECT_ROOT,
    SOURCE_NAME,
    SourceConfig,
    SourceUnavailableError,
    get_source_config,
)

_TIMEOUT_SECONDS = 30
_TZ = timezone(timedelta(hours=8))

OFFICIAL = "OFFICIAL"
PROXY = "PROXY"


# ---------------------------------------------------------------- 官方源
def _official_token() -> str:
    token = os.environ.get("TUSHARE_OFFICIAL_TOKEN", "").strip()
    if token:
        return token
    try:  # 项目 .env 已有官方 TUSHARE_TOKEN 时直接复用，零额外配置
        from dotenv import dotenv_values

        values = dotenv_values(PROJECT_ROOT / ".env")
        return (values.get("TUSHARE_OFFICIAL_TOKEN") or values.get("TUSHARE_TOKEN") or "").strip()
    except Exception:  # pragma: no cover
        return ""


class OfficialTushareProvider:
    """官方 Tushare SDK 适配（主力源）。"""

    name = "official-tushare"

    def __init__(self) -> None:
        self._token = _official_token()
        self._api = None

    @property
    def ready(self) -> bool:
        return bool(self._token)

    def _client(self):
        if self._api is None:
            if not self.ready:
                raise SourceUnavailableError("官方 Tushare Token 未配置")
            import tushare as ts

            self._api = ts.pro_api(self._token)
        return self._api

    def _call(self, api: str, **params) -> pd.DataFrame:
        frame = getattr(self._client(), api)(**params)
        return frame if frame is not None else pd.DataFrame()

    def fund_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._call("fund_daily", ts_code=symbol, start_date=start, end_date=end)

    def fund_adj(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._call("fund_adj", ts_code=symbol, start_date=start, end_date=end)

    def trade_cal(self, start: str, end: str) -> pd.DataFrame:
        return self._call("trade_cal", exchange="SSE", start_date=start, end_date=end)


# ---------------------------------------------------------------- 代理源
class ProxyDataProvider:
    """Tushare 兼容 HTTP 代理适配（备用/高阶源）。"""

    name = SOURCE_NAME

    def __init__(self, config: SourceConfig | None = None) -> None:
        self._config = config or get_source_config()

    @property
    def ready(self) -> bool:
        return self._config.ready

    def _call(self, api_name: str, params: dict) -> pd.DataFrame:
        if not self.ready:
            raise SourceUnavailableError("wealth-lab 代理源未配置（WEALTH_LAB_TS_URL/TOKEN）")
        payload = json.dumps(
            {"api_name": api_name, "token": self._config.token, "params": params, "fields": ""}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._config.url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - 统一转为脱敏的不可用异常
            raise SourceUnavailableError(
                f"代理源请求失败（{api_name}，{type(exc).__name__}）"
            ) from exc
        if body.get("code") != 0:
            raise SourceUnavailableError(
                f"代理源返回错误（{api_name}，code={body.get('code')}）"
            )
        data = body.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])

    def fund_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._call("fund_daily", {"ts_code": symbol, "start_date": start, "end_date": end})

    def fund_adj(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._call("fund_adj", {"ts_code": symbol, "start_date": start, "end_date": end})

    def trade_cal(self, start: str, end: str) -> pd.DataFrame:
        return self._call("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end})


# ---------------------------------------------------------------- 降级编排
@dataclass
class SourcedFrame:
    frame: pd.DataFrame
    source: str  # OFFICIAL / PROXY
    degraded_reason: str | None = None


class DualSourceProvider:
    """优先官方，官方报错（积分/限频/网络）自动降级代理，行级审计。"""

    def __init__(
        self,
        official: OfficialTushareProvider | None = None,
        proxy: ProxyDataProvider | None = None,
    ) -> None:
        self.official = official or OfficialTushareProvider()
        self.proxy = proxy or ProxyDataProvider()
        self.source_status: dict[str, str] = {}

    def _with_fallback(self, api_label: str, official_call, proxy_call) -> SourcedFrame:
        if self.official.ready:
            try:
                frame = official_call()
                self.source_status[api_label] = OFFICIAL
                return SourcedFrame(frame=frame, source=OFFICIAL)
            except Exception as exc:  # noqa: BLE001 - 官方任何失败都降级
                reason = f"{type(exc).__name__}: {str(exc)[:120]}"
        else:
            reason = "official token not configured"
        if not self.proxy.ready:
            raise SourceUnavailableError(
                f"{api_label}：官方源失败（{reason}）且代理源未配置"
            )
        frame = proxy_call()
        self.source_status[api_label] = PROXY
        return SourcedFrame(frame=frame, source=PROXY, degraded_reason=reason)

    def fetch_fund_daily(
        self, symbol: str, start: str, end: str
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        """返回（含 hfq_close 的日线框，接口来源状态）。

        fund_daily 与 fund_adj 各自独立降级；行级 data_source 记行情
        （fund_daily）的拉取源，接口级状态供响应的 source_status 使用。
        """
        daily = self._with_fallback(
            "fund_daily",
            lambda: self.official.fund_daily(symbol, start, end),
            lambda: self.proxy.fund_daily(symbol, start, end),
        )
        adj = self._with_fallback(
            "fund_adj",
            lambda: self.official.fund_adj(symbol, start, end),
            lambda: self.proxy.fund_adj(symbol, start, end),
        )
        frame = daily.frame
        if frame is None or frame.empty:
            return frame.assign(adj_factor=None, hfq_close=None) if frame is not None else frame, dict(self.source_status)
        adj_factors = adj.frame
        if adj_factors is not None and not adj_factors.empty and "adj_factor" in adj_factors.columns:
            factors = adj_factors[["trade_date", "adj_factor"]].copy()
            merged = frame.merge(factors, on="trade_date", how="left")
            merged["adj_factor"] = pd.to_numeric(merged["adj_factor"], errors="coerce")
        else:
            raise SourceUnavailableError("fund_adj 缺少复权因子，不能标记为后复权收益")
        if merged["adj_factor"].isna().any() or (merged["adj_factor"] <= 0).any():
            raise SourceUnavailableError("fund_adj 存在缺失或无效因子，禁止以未来因子或 1 填充")
        merged["hfq_close"] = pd.to_numeric(merged["close"]) * merged["adj_factor"]
        merged["data_source"] = daily.source
        return merged.sort_values("trade_date").reset_index(drop=True), dict(self.source_status)

    def fetch_trade_cal(self, start: str, end: str) -> pd.DataFrame:
        return self._with_fallback(
            "trade_cal",
            lambda: self.official.trade_cal(start, end),
            lambda: self.proxy.trade_cal(start, end),
        ).frame


def source_now() -> datetime:
    return datetime.now(tz=_TZ)
