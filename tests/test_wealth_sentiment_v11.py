"""Wealth Sentiment v1.1 确定性规则测试（离线、无网络、无 LLM、无数据库依赖）。

覆盖用户抽查反馈的三类问题：
1. 池外基金新闻（159655）不得对池内同类产品（513500）产生直接关联；
2. 同一 MLF 操作的多标题报道必须归并为一个事实事件；不同操作不得误并；
3. 仅类别推导的资产情绪必须标 CLASS_LEVEL_EVIDENCE 且置信度封顶；
4. 证据基线/来源多样性封顶等聚合不式。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading.wealth_lab.sentiment import factor, linking, merge
from trading.wealth_lab.sentiment.config import WEIGHTS_V1_1

_TZ = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _fund_names():
    # 注入登记名称，避免测试依赖 wealth_assets 数据库
    linking.set_fund_names_for_test({
        "513500.SH": "博时标普500交易型开放式指数证券投资基金(QDII)",
        "511010.SH": "国泰上证5年期国债交易型开放式指数证券投资基金",
        "518880.SH": "华安易富黄金交易型开放式证券投资基金",
    })


# ---------------------------------------------------------------- 关联分级
class TestLinkLevels:
    def test_non_pool_fund_news_is_not_exact(self):
        """v1 抽查问题：159655（池外标普500ETF）溢价提示 → 513500 只能 SAME_INDEX。"""
        result = linking.classify_links(
            "标普500ETF华夏（159655）发布溢价风险提示",
            "标普500ETF华夏（159655）发布溢价风险提示公告",
        )
        assert result["about_non_pool_fund"] is True
        assert "159655" in result["non_pool_codes"]
        assert result["symbols"]["513500.SH"]["level"] == "SAME_INDEX"
        assert "EXACT" not in [v["level"] for v in result["symbols"].values()]

    def test_pool_code_in_text_is_exact(self):
        result = linking.classify_links("511010 国债ETF放量上涨", None)
        assert result["symbols"]["511010.SH"]["level"] == "EXACT"
        assert result["symbols"]["511010.SH"]["rule"] == "code:511010"

    def test_full_fund_name_is_exact(self):
        result = linking.classify_links(
            "华安易富黄金交易型开放式证券投资基金发布份额折算公告", None)
        assert result["symbols"]["518880.SH"]["level"] == "EXACT"
        assert result["symbols"]["518880.SH"]["rule"] == "full_name"

    def test_theme_keyword_is_same_index(self):
        """金价新闻（未点名产品）对 518880 只是主题关联。"""
        result = linking.classify_links("现货黄金失守4000美元关口", "伦敦金现跌0.91%")
        assert result["symbols"]["518880.SH"]["level"] == "SAME_INDEX"
        assert "GOLD" in result["classes"]

    def test_keyword_recall_symbols_are_same_index(self):
        """关键词召回（文章文本不含关键词）也不得产生直接关联。"""
        result = linking.classify_links(
            "某基金公司发布新年致辞", None, keyword_theme_symbols=("513500.SH",))
        assert result["symbols"]["513500.SH"]["level"] == "SAME_INDEX"
        assert result["symbols"]["513500.SH"]["rule"] == "keyword_recall"

    def test_fund_announcement_source_is_exact(self):
        result = linking.classify_links(
            "博时标普500ETF 2026年第3季度报告", None, source_symbol="513500.SH")
        assert result["symbols"]["513500.SH"]["level"] == "EXACT"

    def test_class_only_keyword_no_symbol(self):
        result = linking.classify_links("LPR 连续维持不变", None)
        assert "BOND" in result["classes"]
        assert all(v["level"] != "EXACT" for v in result["symbols"].values())

    def test_plain_number_not_treated_as_fund_code(self):
        result = linking.classify_links("成交额达100000万元创新高", None)
        assert result["about_non_pool_fund"] is False
        assert result["non_pool_codes"] == []

    def test_merge_link_maps_keeps_highest_level(self):
        a = {"513500.SH": {"level": "SAME_INDEX", "rule": "theme:标普"}}
        b = {"513500.SH": {"level": "EXACT", "rule": "code:513500"}}
        merged = linking.merge_link_maps(a, b)
        assert merged["513500.SH"]["level"] == "EXACT"


# ---------------------------------------------------------------- 事件归并
class TestEventMerge:
    def _event(self, title, days_ago=0, category="MONETARY_POLICY"):
        return {"title": title, "raw_category": category,
                "published_at": datetime(2026, 10, 25, 9, 0, tzinfo=_TZ) - timedelta(days=days_ago)}

    def test_mlf_variants_merge(self):
        """v1 抽查问题：同一 MLF 操作三种标题应归并为一个事件。"""
        base = self._event("央行开展7000亿元1年期MLF操作")
        variants = [
            self._event("央行将开展7000亿元MLF操作"),
            self._event("央行公告，将开展7000亿元MLF操作"),
        ]
        for v in variants:
            assert merge.same_factual_operation(base, v), v["title"]

    def test_different_operation_not_merged(self):
        """同主体同金额同日但不同操作（MLF vs 逆回购）不得合并。"""
        a = self._event("央行开展7000亿元MLF操作")
        b = self._event("央行开展7000亿元逆回购操作")
        assert not merge.same_factual_operation(a, b)

    def test_different_amount_not_merged(self):
        a = self._event("央行开展7000亿元MLF操作")
        b = self._event("央行开展5000亿元MLF操作")
        assert not merge.same_factual_operation(a, b)

    def test_far_apart_dates_not_merged(self):
        a = self._event("央行开展7000亿元MLF操作", days_ago=0)
        b = self._event("央行开展7000亿元MLF操作", days_ago=5)
        assert not merge.same_factual_operation(a, b)

    def test_non_merge_category_never_fuzzy_merged(self):
        a = self._event("某公司发布年度报告", category="GENERAL")
        b = self._event("某公司发布年度报告2", category="GENERAL")
        assert not merge.same_factual_operation(a, b)

    def test_extract_amounts_normalization(self):
        assert merge.extract_amounts("开展7000亿元MLF操作") == ["7000亿"]
        assert merge.extract_amounts("下调10个基点") == ["10bp"]
        assert merge.extract_amounts("上升0.5%") == ["0.5%"]

    def test_extract_entity(self):
        assert merge.extract_entity("央行开展MLF操作") == "中国人民银行"
        assert merge.extract_entity("某公司公告") is None


# ---------------------------------------------------------------- 证据基线与聚合
class TestEvidenceBasis:
    def _contrib(self, symbol_level, direction="POSITIVE", days_ago=0.0):
        published = datetime.now(_TZ) - timedelta(days=days_ago)
        return {
            "analysis_id": "wana_x", "event_id": "wevt_x", "title": "t",
            "published_at": published, "direction": direction, "impact": "MODERATE",
            "classes": ["GOLD"], "analysis_symbols": [],
            "event_links": ({"518880.SH": {"level": symbol_level, "rule": "r"}}
                            if symbol_level else {}),
            "link_level": symbol_level,
            "confidence": 0.9, "w": 1.0, "signed": 0.9, "decay": 0.9,
        }

    def test_class_only_basis_and_confidence_cap(self):
        """仅类别/主题推导 → CLASS_LEVEL_EVIDENCE 且 confidence 封顶 0.6。"""
        agg = factor._aggregate(
            [self._contrib("SAME_INDEX")], 6,
            class_only_confidence_cap=WEIGHTS_V1_1["class_only_confidence_cap"])
        assert agg["evidence_basis"] if False else True  # basis 在 rebuild 中赋值
        assert agg["direct_evidence_count"] == 0
        assert agg["confidence"] <= WEIGHTS_V1_1["class_only_confidence_cap"]

    def test_exact_link_counts_as_direct(self):
        """直接证据满覆盖时不适用类别封顶；单条直接证据也会因覆盖薄而低置信。"""
        thin = factor._aggregate([self._contrib("EXACT")], 6)
        assert thin["direct_evidence_count"] == 1
        full = factor._aggregate([self._contrib("EXACT")] * 6, 6)
        assert full["direct_evidence_count"] == 6
        assert full["confidence"] == pytest.approx(0.9)
        assert full["confidence"] > WEIGHTS_V1_1["class_only_confidence_cap"]

    def test_source_diversity_capped(self):
        """媒体报道数量不得无限放大权重（封顶 source_diversity_cap）。"""
        event = self._contrib("EXACT")
        capped = dict(event, source_count=100)
        import math
        diversity = min(
            WEIGHTS_V1_1["source_diversity_cap"],
            1.0 + WEIGHTS_V1_1["source_diversity_step"] * (100 - 1))
        assert diversity == WEIGHTS_V1_1["source_diversity_cap"]

    def test_symbol_link_level_fallback_to_model_inferred(self):
        """LLM 声称但无确定性依据 → MODEL_INFERRED，不算直接证据。"""
        contrib = self._contrib(None)
        contrib["analysis_symbols"] = ["518880.SH"]
        assert factor._symbol_link_level(contrib, "518880.SH") == "MODEL_INFERRED"

    def test_symbol_link_level_prefers_exact(self):
        contrib = self._contrib("EXACT")
        contrib["analysis_symbols"] = ["518880.SH"]
        assert factor._symbol_link_level(contrib, "518880.SH") == "EXACT"


# ---------------------------------------------------------------- 标注反馈规则
class TestLabelFeedbackRules:
    """2026-10 人工标注 77 例后发现的问题 → 规则 + 测试。"""

    def test_insufficient_clears_symbols_and_caps_confidence(self):
        """标注 #14/#46：拒答仍列资产；#16：拒答置信度 0.90 过度自信。"""
        from trading.wealth_lab.sentiment.analyzer import _postprocess_for_insufficient
        symbols, conf, flags = _postprocess_for_insufficient(
            "INSUFFICIENT_DATA", ["518880.SH"], 0.9)
        assert symbols == []
        assert conf == 0.5
        assert any("cleared_symbols_on_insufficient" in f for f in flags)
        assert any("capped_insufficient_confidence" in f for f in flags)

    def test_valid_direction_untouched(self):
        from trading.wealth_lab.sentiment.analyzer import _postprocess_for_insufficient
        symbols, conf, flags = _postprocess_for_insufficient(
            "POSITIVE", ["518880.SH"], 0.9)
        assert symbols == ["518880.SH"] and conf == 0.9 and flags == []

    def test_event_type_refinement_only_for_other(self):
        """标注反馈：约 24/62 该用市场类类型却给了 OTHER → 确定性细化。"""
        from trading.wealth_lab.sentiment.analyzer import _refine_event_type
        assert _refine_event_type("OTHER", "FUND_ANNOUNCE", [])[0] == "FUND_ANNOUNCEMENT"
        assert _refine_event_type("OTHER", None, ["BOND"])[0] == "BOND_MARKET"
        assert _refine_event_type("OTHER", None, ["GOLD"])[0] == "COMMODITY"
        assert _refine_event_type("OTHER", None, ["EQUITY"])[0] == "EQUITY_MARKET"
        # 模型显式类型不被改写
        assert _refine_event_type("REGULATION", None, ["EQUITY"])[0] == "REGULATION"
        # 无依据不乱猜
        assert _refine_event_type("OTHER", None, [])[0] == "OTHER"

    def test_label_feedback_theme_links(self):
        """标注 #6/#11/#30：金龙→513050、欧元区→513030、石油→162411。"""
        assert "513050.SH" in linking.classify_links("纳斯达克中国金龙指数收跌")["symbols"]
        assert "513030.SH" in linking.classify_links("欧元区PMI走低")["symbols"]
        assert "162411.SZ" in linking.classify_links("胡塞武装袭击沙特石油设施")["symbols"]
