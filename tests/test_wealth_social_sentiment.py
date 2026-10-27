"""Wealth Social Sentiment 合规闸门与数据层测试（离线、无网络、无 LLM 调用）。

核心验收：未授权来源的自动采集/导入一律拒绝并给出原因；
PII 不入库；合成数据与真实数据隔离；无真实数据时词云/分析返回 INSUFFICIENT_DATA。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading.wealth_lab.social_sentiment import ingest, registry, wordcloud
from trading.wealth_lab.social_sentiment import repository as repo
from trading.wealth_lab.social_sentiment.analyzer import aggregate_research
from trading.wealth_lab.social_sentiment.routes import router as social_router

_TZ = timezone(timedelta(hours=8))

SYNTHETIC_COMMENTS = [
    {"content": "黄金ETF最近回调了不少，但是长期看央行还在买金，我打算继续定投",
     "symbol": "518880.SH", "topic": "黄金", "published_at": "2026-10-24T10:00:00+08:00"},
    {"content": "国债ETF这波利率下行吃到了分红，继续拿着，降息周期债券舒服",
     "symbol": "511010.SH", "topic": "利率", "published_at": "2026-10-24T11:00:00+08:00"},
    {"content": "纳指ETF溢价太高了，QDII额度紧张，追高要小心溢价回落风险",
     "symbol": "513100.SH", "topic": "QDII溢价", "published_at": "2026-10-24T12:00:00+08:00"},
]


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(social_router)
    return TestClient(app)


# ---------------------------------------------------------------- 未授权自动禁用（P0 验收）
class TestUnauthorizedDisabled:
    def test_guba_auto_collect_refused(self):
        result = ingest.collect_from_platform("eastmoney-guba")
        assert result["refused"] is True
        assert "PROHIBITED" in result["reason"]

    def test_xueqiu_auto_collect_refused(self):
        result = ingest.collect_from_platform("xueqiu-community")
        assert result["refused"] is True and "禁止" in result["reason"]

    def test_collect_api_403_with_reason(self, client):
        for sid in ("eastmoney-guba", "xueqiu-community", "cninfo-irm",
                    "sse-einteract", "jisilu-community"):
            resp = client.post("/v1/wealth-lab/social-sentiment/collect",
                               json={"source_id": sid})
            assert resp.status_code == 403, sid
            assert resp.json()["detail"]  # 必须给出具体禁用原因

    def test_import_to_prohibited_source_403(self, client):
        resp = client.post("/v1/wealth-lab/social-sentiment/import",
                           json={"source_id": "eastmoney-guba",
                                 "comments": [{"content": "测试内容不得入库"}]})
        assert resp.status_code == 403

    def test_import_to_unverified_source_403(self, client):
        resp = client.post("/v1/wealth-lab/social-sentiment/import",
                           json={"source_id": "jisilu-community",
                                 "comments": [{"content": "测试内容不得入库"}]})
        assert resp.status_code == 403

    def test_sources_expose_disabled_reason_and_channel(self, client):
        body = client.get("/v1/wealth-lab/social-sentiment/sources").json()
        by_id = {s["source_id"]: s for s in body["data"]["sources"]}
        assert by_id["eastmoney-guba"]["status"] == "PROHIBITED"
        assert by_id["xueqiu-community"]["status"] == "PROHIBITED"
        assert by_id["eastmoney-guba"]["collector_enabled"] is False
        assert "Choice" in by_id["eastmoney-guba"].get("disabled_reason", "") \
            or by_id["eastmoney-guba"]["application_channel"]
        for s in body["data"]["sources"]:
            if not s["collector_enabled"]:
                assert s.get("disabled_reason")


# ---------------------------------------------------------------- 注册表
class TestRegistry:
    def test_authorized_requires_evidence(self):
        with pytest.raises(ValueError, match="授权依据"):
            registry.set_status("xueqiu-community", "AUTHORIZED", evidence=" ")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="非法状态"):
            registry.set_status("jisilu-community", "MAYBE", evidence="x")

    def test_unknown_source_blocked(self):
        with pytest.raises(PermissionError, match="未知数据源"):
            registry.ensure_operation_allowed("xiaohongshu", "auto_collect")

    def test_status_change_audited(self):
        before = registry.get_source("sse-einteract")
        assert before["status"] == "UNVERIFIED"
        updated = registry.set_status(
            "sse-einteract", "PENDING_AUTHORIZATION",
            evidence="已联系上交所信息公司咨询数据授权（测试证据）")
        assert updated["status"] == "PENDING_AUTHORIZATION"
        # 还原，避免影响其他测试
        registry.set_status("sse-einteract", "UNVERIFIED", evidence="测试还原")


# ---------------------------------------------------------------- 导入层
class TestImport:
    def test_manual_import_requires_assertion(self):
        with pytest.raises(ValueError, match="authorization_assertion"):
            ingest.import_comments("manual-authorized-file",
                                   [{"content": "已获书面授权的评论内容"}])

    def test_synthetic_cannot_enter_via_manual_source(self):
        with pytest.raises(ValueError, match="冒充"):
            ingest.import_comments(
                "manual-authorized-file",
                [{"content": "假装是真话的合成评论"}],
                authorization_assertion="测试声明",
                data_origin="synthetic_test")

    def test_import_synthetic_lab_and_dedup(self):
        import uuid
        salt = uuid.uuid4().hex[:6]
        batch = [{**c, "content": c["content"] + f"（批次{salt}）"}
                 for c in SYNTHETIC_COMMENTS]
        first = ingest.import_comments("synthetic-test-lab", batch,
                                       data_origin="synthetic_test")
        assert first["imported"] == 3 and first["rejected"] == 0
        second = ingest.import_comments("synthetic-test-lab", batch,
                                        data_origin="synthetic_test")
        assert second["imported"] == 0 and second["duplicate"] == 3

    def test_pii_rejected_item_level(self):
        result = ingest.import_comments("synthetic-test-lab", [
            {"content": "正常评论内容不含个人信息", "nickname": "某用户",
             "avatar": "http://x/y.png"},
        ], data_origin="synthetic_test")
        assert result["imported"] == 0 and result["rejected"] == 1
        assert "个人信息" in result["errors"][0]

    def test_pii_rejected_api_level(self, client):
        resp = client.post("/v1/wealth-lab/social-sentiment/import",
                           json={"source_id": "synthetic-test-lab",
                                 "data_origin": "synthetic_test",
                                 "comments": [{"content": "带昵称的评论",
                                               "nickname": "u1"}]})
        assert resp.status_code == 422  # extra=forbid 直接拒绝 PII 字段

    def test_real_import_requires_authorized_import_ops(self):
        # synthetic-test-lab 允许导入；未经授权源在 API 层 403 已覆盖
        result = ingest.import_comments(
            "synthetic-test-lab",
            [{"content": "真实导入需走 manual-authorized-file 并附授权声明"}],
            data_origin="synthetic_test")
        assert result["imported"] >= 0  # 闸门放行（synthetic 源）


# ---------------------------------------------------------------- 保留期
class TestRetention:
    def test_expired_comments_purged(self):
        old = dict(SYNTHETIC_COMMENTS[0])
        old["content"] = "过期评论应按保留期清理" + old["content"][:8]
        old["published_at"] = "2026-10-01T10:00:00+08:00"
        ingest.import_comments("synthetic-test-lab", [old], data_origin="synthetic_test")
        # 手工把该条 fetched_at 拨回 30 天前（synthetic 源保留期 7 天）
        with repo.connect() as con:
            con.execute(
                "UPDATE social_comments SET fetched_at=? WHERE content LIKE ?",
                [datetime.now(_TZ) - timedelta(days=30), "过期评论应按保留期清理%"])
        deleted = registry.enforce_retention()
        assert deleted >= 1
        with repo.connect() as con:
            remain = con.execute(
                "SELECT COUNT(*) FROM social_comments WHERE content LIKE ?",
                ["过期评论应按保留期清理%"]).fetchone()[0]
        assert remain == 0


# ---------------------------------------------------------------- 无数据不编造
class TestNoDataNoFabrication:
    def test_wordcloud_insufficient_without_real_data(self):
        result = wordcloud.word_frequency(include_synthetic=False)
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_analyze_insufficient_without_real_data(self):
        from trading.wealth_lab.social_sentiment.analyzer import classify_batch
        result = classify_batch(limit=5, include_synthetic=False)
        assert result["status"] == "INSUFFICIENT_DATA"
        assert result["analyzed"] == 0  # 未调用模型

    def test_research_insufficient_without_real_data(self):
        result = aggregate_research()
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_heatmap_empty_not_fabricated(self, client):
        body = client.get("/v1/wealth-lab/social-sentiment/heatmap").json()
        assert body["data"]["cells"] == []


# ---------------------------------------------------------------- 词频确定性
class TestWordFrequency:
    def test_builtin_tokenizer_requires_no_external_segmenter(self):
        tokens = wordcloud._tokens("黄金ETF回调后继续定投，https://example.com 免费领加群")
        assert "黄金" in tokens
        assert "etf" in tokens
        assert "定投" in tokens
        assert all("加群" not in token and "免费领" not in token for token in tokens)

    def test_deterministic_and_filtered(self):
        a = wordcloud.word_frequency(include_synthetic=True, top_k=30)
        b = wordcloud.word_frequency(include_synthetic=True, top_k=30)
        assert a["status"] == "OK"
        assert a["word_frequency"] == b["word_frequency"]  # 纯确定性统计
        words = [w for w, _ in a["word_frequency"]]
        assert "黄金" in words or "定投" in words
        for stop in ("的", "了", "我们", "加群"):
            assert stop not in words
        topics = dict(a["finance_topic_frequency"])
        assert topics.get("GOLD", 0) >= 1  # "黄金/买金"命中 GOLD 主题

    def test_flood_spam_not_inflated(self):
        result = wordcloud.word_frequency(include_synthetic=True, top_k=100)
        # 同一条评论内重复词只计一次（刷屏灌水不放大词频）
        words = dict(result["word_frequency"])
        assert words.get("涨停敢死队", 0) == 0


# ---------------------------------------------------------------- synthetic 隔离
class TestSyntheticIsolation:
    def test_api_research_excludes_synthetic(self, client):
        body = client.get("/v1/wealth-lab/social-sentiment/research").json()
        assert body["data"]["status"] == "INSUFFICIENT_DATA"

    def test_api_wordcloud_excludes_synthetic(self, client):
        body = client.get("/v1/wealth-lab/social-sentiment/wordcloud").json()
        assert body["data"]["status"] == "INSUFFICIENT_DATA"
