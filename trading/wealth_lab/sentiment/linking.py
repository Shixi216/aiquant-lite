"""确定性资产关联分级（v1.1 P0）。

四级关联：EXACT（基金代码/完整登记名称出现）> SAME_INDEX（同一指数或主题，
含"另一同类基金产品"新闻）> CLASS_ONLY（仅类别关键词）> MODEL_INFERRED
（LLM 推断，由校验层赋予，本模块不产生）。

核心规则：池外基金新闻（出现非池内 6 位基金代码）不得把主题关联伪装成
直接关联——EXACT 只能来自池内代码或完整登记名称。
全部为纯函数，便于单元测试。
"""
from __future__ import annotations

import re

from trading.wealth_lab.sentiment.config import (
    FUND_CODE_TO_SYMBOL, LINK_LEVEL_RANK, SYMBOL_CLASS, TEXT_LINK_RULES,
)

# 6 位场内基金代码段：ETF 51/56/58、LOF 15/16、REITs 18/50（避免普通数字误命中）
_CODE_RE = re.compile(r"(?<!\d)(?:15|16|18|50|51|56|58)\d{4}(?!\d)")


def _pool_fund_names() -> dict[str, str]:
    """symbol → 完整登记名称（从 wealth_assets 惰性加载；失败时降级为空）。"""
    global _FUND_NAME_CACHE
    if _FUND_NAME_CACHE is None:
        try:
            from trading.wealth_lab.universe import assets_report
            _FUND_NAME_CACHE = {a["symbol"]: a.get("name") or ""
                                for a in assets_report()}
        except Exception:  # noqa: BLE001 - 单测/离线环境允许无库
            _FUND_NAME_CACHE = {}
    return _FUND_NAME_CACHE


_FUND_NAME_CACHE: dict[str, str] | None = None


def set_fund_names_for_test(names: dict[str, str]) -> None:
    """测试注入登记名称，避免依赖数据库。"""
    global _FUND_NAME_CACHE
    _FUND_NAME_CACHE = dict(names)


def normalize_text(title: str, summary: str | None) -> str:
    return f"{title or ''} {summary or ''}"


def extract_codes(text: str) -> list[str]:
    return _CODE_RE.findall(text or "")


def classify_links(title: str, summary: str | None = None,
                   source_symbol: str | None = None,
                   keyword_theme_symbols: tuple[str, ...] | list[str] = ()) -> dict:
    """确定性关联分级。

    source_symbol：基金公告适配器传入的"公告所属基金"（公告本身就是该产品
    的事实文件 → EXACT）。关键词资讯源不得使用该参数。
    keyword_theme_symbols：关键词检索命中的主题相关资产（文章由该关键词召回，
    但文本未点名代码/全名）→ SAME_INDEX，绝不升级为直接证据。
    返回：
      {
        "symbols": {symbol: {"level": "EXACT|SAME_INDEX", "rule": str}},
        "classes": [class, ...],
        "about_non_pool_fund": bool,   # 文本点名了池外基金产品
        "non_pool_codes": [code, ...],
        "pool_codes": [code, ...],
      }
    """
    text = normalize_text(title, summary)
    codes = extract_codes(text)
    pool_codes = [c for c in codes if c in FUND_CODE_TO_SYMBOL]
    non_pool_codes = [c for c in codes if c not in FUND_CODE_TO_SYMBOL]

    symbols: dict[str, dict] = {}

    # 1) 池内基金代码出现 → EXACT（不可被池外标志降级：新闻同时点名即事实）
    for code in pool_codes:
        sym = FUND_CODE_TO_SYMBOL[code]
        symbols[sym] = {"level": "EXACT", "rule": f"code:{code}"}

    # 2) 完整登记名称出现 → EXACT（保守：仅完整名称，避免"国债ETF"泛称误配）
    for sym, name in _pool_fund_names().items():
        if name and len(name) >= 6 and name in text and sym not in symbols:
            symbols[sym] = {"level": "EXACT", "rule": "full_name"}

    # 3) 基金公告源的产品本身 → EXACT
    if source_symbol and source_symbol in SYMBOL_CLASS and source_symbol not in symbols:
        symbols[source_symbol] = {"level": "EXACT", "rule": "source_announcement"}

    # 4) 主题/指数关键词 → SAME_INDEX（同一标的或主题；池外产品新闻同样只配这一级）
    classes: list[str] = []
    for keyword, klass, syms in TEXT_LINK_RULES:
        if keyword in text:
            if klass and klass not in classes:
                classes.append(klass)
            for sym in syms:
                if sym not in symbols:
                    symbols[sym] = {"level": "SAME_INDEX", "rule": f"theme:{keyword}"}

    # 5) 关键词召回的主题资产 → SAME_INDEX（非直接证据）
    for sym in keyword_theme_symbols:
        if sym in SYMBOL_CLASS and sym not in symbols:
            symbols[sym] = {"level": "SAME_INDEX", "rule": "keyword_recall"}

    for sym in list(symbols):  # 代码/名称命中的补类别
        klass = SYMBOL_CLASS.get(sym)
        if klass and klass not in classes:
            classes.append(klass)

    return {
        "symbols": symbols,
        "classes": classes,
        "about_non_pool_fund": bool(non_pool_codes),
        "non_pool_codes": sorted(set(non_pool_codes)),
        "pool_codes": sorted(set(pool_codes)),
    }


def merge_link_maps(*maps: dict) -> dict:
    """多来源/多事件链接合并：symbol 取最高层级，rule 记录来源。"""
    merged: dict[str, dict] = {}
    for mp in maps:
        for sym, info in (mp or {}).items():
            cur = merged.get(sym)
            if cur is None or LINK_LEVEL_RANK[info["level"]] < LINK_LEVEL_RANK[cur["level"]]:
                merged[sym] = dict(info)
            elif info["level"] == cur["level"] and info["rule"] not in cur["rule"]:
                merged[sym] = {**cur, "rule": f"{cur['rule']}|{info['rule']}"}
    return merged
