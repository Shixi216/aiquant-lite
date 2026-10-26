"""Wealth Lab 配置：资产清单、默认权重与自建数据源。

数据源凭证仅从本地环境变量 / .env 读取（WEALTH_LAB_TS_URL / WEALTH_LAB_TS_TOKEN），
绝不硬编码、绝不进入 git 与日志。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WEALTH_LAB_DB_PATH = PROJECT_ROOT / "database" / "wealth_lab.duckdb"

ENGINE_VERSION = "wealth-lab-backtest/1.0"
CONTRACT_VERSION = "wealth-lab/v1"
SOURCE_NAME = "xiaodefa-tushare"

# 契约 v1 冻结的资产清单与默认权重（40/40/10/10）
ASSETS: dict[str, dict] = {
    "510300.SH": {
        "name": "沪深300ETF",
        "category": "EQUITY_ETF",
        "default_weight": 0.40,
        "listing_date": "2012-05-28",
    },
    "511010.SH": {
        "name": "国债ETF",
        "category": "BOND_ETF",
        "default_weight": 0.40,
        "listing_date": "2013-03-25",
    },
    "518880.SH": {
        "name": "黄金ETF",
        "category": "GOLD_ETF",
        "default_weight": 0.10,
        "listing_date": "2013-07-29",
    },
    "511380.SH": {
        "name": "可转债ETF",
        "category": "CONVERTIBLE_ETF",
        "default_weight": 0.10,
        "listing_date": "2020-03-12",
    },
}

DEFAULT_WEIGHTS: dict[str, float] = {
    symbol: meta["default_weight"] for symbol, meta in ASSETS.items()
}

DEFAULT_BENCHMARKS = ["EQUAL_WEIGHT", "510300.SH"]

RF_ANNUAL = 0.02  # Sharpe 无风险利率（契约注明）
TRADING_DAYS_PER_YEAR = 252


class SourceUnavailableError(RuntimeError):
    """自建数据源不可达或未配置。"""


@dataclass(frozen=True)
class SourceConfig:
    url: str
    token: str

    @property
    def ready(self) -> bool:
        return bool(self.url and self.token)


def get_source_config() -> SourceConfig:
    url = os.environ.get("WEALTH_LAB_TS_URL", "").strip()
    token = os.environ.get("WEALTH_LAB_TS_TOKEN", "").strip()
    if not (url and token):
        try:  # 独立脚本运行时 .env 可能尚未加载
            from dotenv import dotenv_values

            values = dotenv_values(PROJECT_ROOT / ".env")
            url = url or (values.get("WEALTH_LAB_TS_URL") or "").strip()
            token = token or (values.get("WEALTH_LAB_TS_TOKEN") or "").strip()
        except Exception:  # pragma: no cover - dotenv 缺失时保持不可用语义
            pass
    return SourceConfig(url=url.rstrip("/") + "/", token=token)
