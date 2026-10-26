# -*- coding: utf-8 -*-
"""v3.1 P0-1/P1-3：分类修正（基于 fund_basic 全名人工核对）+ 维度扩展 + 审计。

分类依据：fund_basic 官方全名与 fund_type（2026-10-24 拉取）逐一人工核对；
162411.SZ 修正为 QDII-LOF 海外权益（跟踪标普石油天然气上游股票指数）。
"""
from __future__ import annotations

import json
from pathlib import Path

from trading.wealth_lab.universe import assets_report, register_universe, upsert_assets

# (category, product_form, underlying, market_scope, currency_risk, note)
# underlying: CN_EQUITY / OVERSEAS_EQUITY / CN_BOND / CONVERTIBLE /
#             COMMODITY_GOLD / COMMODITY_FUTURES / REIT
CLASSIFICATION: dict[str, dict] = {
    # 宽基权益
    "510300.SH": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", ""),
    "510500.SH": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", ""),
    "159915.SZ": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", ""),
    "588000.SH": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", ""),
    "159949.SZ": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", ""),
    "510880.SH": ("CN_EQUITY_BROAD_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "红利风格（宽基登记）"),
    # 行业
    "512480.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "半导体"),
    "515030.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "新能源汽车"),
    "512170.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "医疗"),
    "512690.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "酒"),
    "512800.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "银行"),
    "512660.SH": ("CN_INDUSTRY_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "军工"),
    # 风格
    "512890.SH": ("CN_STYLE_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "红利低波"),
    "563020.SH": ("CN_STYLE_ETF", "ETF", "CN_EQUITY", "DOMESTIC", "NONE", "红利低波"),
    "159545.SZ": ("CN_STYLE_ETF", "ETF", "OVERSEAS_EQUITY", "HK_CONNECT", "HKD",
                  "港股通红利低波：底层为恒生指数系列（境外市场），经港股通通道投资"),
    # 债券
    "511010.SH": ("CN_BOND_ETF", "ETF", "CN_BOND", "DOMESTIC", "NONE", "5年国债"),
    "511260.SH": ("CN_BOND_ETF", "ETF", "CN_BOND", "DOMESTIC", "NONE", "10年国债"),
    "511090.SH": ("CN_BOND_ETF", "ETF", "CN_BOND", "DOMESTIC", "NONE", "30年国债"),
    "511520.SH": ("CN_BOND_ETF", "ETF", "CN_BOND", "DOMESTIC", "NONE", "政金债7-10年"),
    "159972.SZ": ("CN_BOND_ETF", "ETF", "CN_BOND", "DOMESTIC", "NONE", "5年地方债"),
    "511380.SH": ("CN_CONVERTIBLE_ETF", "ETF", "CONVERTIBLE", "DOMESTIC", "NONE",
                  "可转债及可交换债（含信用与转股溢价风险）"),
    # 商品
    "518880.SH": ("CN_COMMODITY_ETF", "ETF", "COMMODITY_GOLD", "DOMESTIC", "RMB_GOLD_PRICE",
                  "境内黄金现货合约，人民币金价格风险"),
    "159985.SZ": ("CN_COMMODITY_ETF", "ETF", "COMMODITY_FUTURES", "DOMESTIC", "NONE",
                  "豆粕期货 ETF（展期成本影响）"),
    # 修正项：华宝油气为 QDII-LOF 海外权益
    "162411.SZ": ("QDII_LOF", "LOF", "OVERSEAS_EQUITY", "OVERSEAS", "USD_MULTI",
                  "【v3.1 修正】原误分类 CN_COMMODITY_ETF。fund_basic 全名：华宝标普石油天然气"
                  "上游股票指数(QDII-LOF)-A-CNY，fund_type 股票型；底层为海外股票指数（能源行业），"
                  "非商品期货；人民币份额 A 类，美元多资产汇率风险"),
    # 转债 ETF 已列
    # QDII ETF
    "513100.SH": ("QDII_ETF", "ETF", "OVERSEAS_EQUITY", "OVERSEAS", "USD_MULTI", "纳斯达克100"),
    "513500.SH": ("QDII_ETF", "ETF", "OVERSEAS_EQUITY", "OVERSEAS", "USD_MULTI", "标普500"),
    "513050.SH": ("QDII_ETF", "ETF", "OVERSEAS_EQUITY", "OVERSEAS", "USD_MULTI", "中概互联（港/美）"),
    "510900.SH": ("QDII_ETF", "ETF", "OVERSEAS_EQUITY", "OVERSEAS", "HKD", "恒生中国企业"),
    "513030.SH": ("QDII_ETF", "ETF", "OVERSEAS_EQUITY", "OVERSEAS", "EUR_MULTI", "德国DAX"),
    # REITs
    "508056.SH": ("CN_REIT", "REIT", "REIT", "DOMESTIC", "NONE", "仓储物流；fund_adj 含分配再投口径"),
    "180101.SZ": ("CN_REIT", "REIT", "REIT", "DOMESTIC", "NONE", "产业园；fund_adj 含分配再投口径"),
    "508000.SH": ("CN_REIT", "REIT", "REIT", "DOMESTIC", "NONE", "产业园；fund_adj 含分配再投口径"),
    "180201.SZ": ("CN_REIT", "REIT", "REIT", "DOMESTIC", "NONE", "高速公路；fund_adj 含分配再投口径"),
}


def main() -> None:
    assets = assets_report()
    changes = []
    for a in assets:
        symbol = a["symbol"]
        if symbol not in CLASSIFICATION:
            changes.append({"symbol": symbol, "issue": "未在核对映射中（保持原分类）"})
            continue
        category, form, underlying, market, currency, note = CLASSIFICATION[symbol]
        old_category = a["category"]
        a.update({
            "category": category, "product_form": form, "underlying": underlying,
            "market_scope": market, "currency_risk": currency,
            "notes": (note or a.get("notes", "")),
        })
        if old_category != category:
            changes.append({"symbol": symbol, "from": old_category, "to": category, "reason": note[:60]})
    upsert_assets(assets)

    # 新版资产池：分类修正版（v1 保留作历史版本）
    qualified = [a["symbol"] for a in assets if a["quality_status"] == "QUALIFIED"]
    register_universe(
        "multi-asset-v2", "2.0", "多资产配置池（v3.1 分类修正）",
        "162411 修正为 QDII_LOF（海外权益）；159545 标注港股通；商品类实际为黄金+豆粕 2 只",
        qualified,
    )
    domestic_commodity = [a["symbol"] for a in assets
                          if a.get("underlying", "").startswith("COMMODITY")]
    report = {
        "total": len(assets),
        "category_changes": changes,
        "dimension_summary": {
            "product_form": {f: sum(1 for a in assets if a.get("product_form") == f)
                             for f in ("ETF", "LOF", "REIT")},
            "market_scope": {m: sum(1 for a in assets if a.get("market_scope") == m)
                             for m in ("DOMESTIC", "HK_CONNECT", "OVERSEAS")},
            "domestic_commodity_after_fix": domestic_commodity,
        },
        "new_universe": "multi-asset-v2",
    }
    Path("runtime/_v31_reclassification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    for a in assets:
        if a["symbol"] in ("162411.SZ", "159545.SZ"):
            print("验证:", a["symbol"], a["category"], a["product_form"], a["underlying"],
                  a["market_scope"], a["currency_risk"])


if __name__ == "__main__":
    main()
