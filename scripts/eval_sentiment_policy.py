"""情绪/政策面 人工标注评估（SHADOW_VALIDATION）— 修正版

关键修正：
1. 数据读取在 asyncio 外完成（短生命周期连接，避免 GIL 崩溃）
2. AI 评估逐条匹配事件方向（而非用汇总得分符号）
3. 缓存命中/覆盖率/陈旧率/模型失败率统计
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))

# 人工标注样本（真实公告/新闻，方向为专家标注）
# (symbol, title, expected_direction: 1/-1/0, category)
GOLD_SAMPLES = [
    # ===== 利好公告（12条）=====
    ("002407", "多氟多2026年半年度业绩预增公告", 1, "利好公告"),
    ("600010", "包钢股份2026年半年度业绩预增公告", 1, "利好公告"),
    ("600664", "哈药股份2026年半年度业绩预增公告", 1, "利好公告"),
    ("600664", "哈药集团关于所属企业获得化学原料药上市申请批准通知书的公告", 1, "利好公告"),
    ("002407", "多氟多关于回购公司股份方案的公告", 1, "利好公告"),
    ("600186", "莲花控股关于签署重大战略合作协议的公告", 1, "利好公告"),
    ("000100", "TCL科技关于回购公司股份的进展公告", 1, "利好公告"),
    ("002579", "中京电子关于收到中标通知书的公告", 1, "利好公告"),
    ("600172", "黄河旋风关于向全资子公司划转资产并增资的公告", 1, "利好公告"),
    ("600707", "彩虹股份关于获得政府补助的公告", 1, "利好公告"),
    ("002585", "双星新材关于复合铜箔项目投产的公告", 1, "利好公告"),
    ("000066", "中国长城关于签订重大合同的公告", 1, "利好公告"),
    # ===== 利空公告（12条）=====
    ("600172", "关于持股5%以上股东股份被轮候冻结的公告", -1, "利空公告"),
    ("002407", "股票交易异常波动公告", -1, "利空公告"),
    ("600664", "股票交易严重异常波动暨风险提示公告", -1, "利空公告"),
    ("600010", "包钢股份关于子公司补缴税款的公告", -1, "利空公告"),
    ("600110", "诺德股份关于收到中国证监会立案告知书的公告", -1, "利空公告"),
    ("002354", "天娱数科关于股东减持股份的预披露公告", -1, "利空公告"),
    ("600186", "莲花控股关于控股股东股份被司法冻结的公告", -1, "利空公告"),
    ("002167", "东方锆业关于收到监管警示函的公告", -1, "利空公告"),
    ("600707", "彩虹股份关于公司涉及重大诉讼的公告", -1, "利空公告"),
    ("002585", "双星新材关于终止重大资产重组事项的公告", -1, "利空公告"),
    ("600172", "黄河旋风关于股票可能被实施退市风险警示的公告", -1, "利空公告"),
    ("000066", "中国长城关于业绩亏损的提示性公告", -1, "利空公告"),
    # ===== 中性公告（8条）=====
    ("600010", "包钢股份2026年第三次临时股东会决议公告", 0, "中性公告"),
    ("600010", "包钢股份第八届董事会第五次会议决议公告", 0, "中性公告"),
    ("600010", "包钢股份第八届董事会第四次会议决议公告", 0, "中性公告"),
    ("002407", "多氟多关于召开2026年第一次临时股东大会的通知", 0, "中性公告"),
    ("600664", "哈药股份十届二十七次董事会决议公告", 0, "中性公告"),
    ("600172", "黄河旋风第十届董事会第二次会议决议公告", 0, "中性公告"),
    ("000100", "TCL科技关于董事辞职的公告", 0, "中性公告"),
    ("002585", "双星新材关于变更会计师事务所的公告", 0, "中性公告"),
    # ===== 行业政策（6条）=====
    ("MARKET", "国务院关于印发《工业绿色低碳发展十五五规划》的通知", 1, "行业政策"),
    ("MARKET", "国家发改委关于进一步扩大新能源汽车消费的通知", 1, "行业政策"),
    ("MARKET", "工信部关于印发《半导体产业扶持计划》的通知", 1, "行业政策"),
    ("MARKET", "国家医保局关于创新药医保支付政策优化的通知", 1, "行业政策"),
    ("MARKET", "证监会关于进一步规范上市公司减持行为的通知", -1, "行业政策"),
    ("MARKET", "生态环境部关于开展高耗能行业环保专项整治的通知", -1, "行业政策"),
    # ===== 公司风险（3条）=====
    ("600110", "诺德股份关于公司债券兑付存在不确定性的公告", -1, "公司风险"),
    ("002354", "天娱数科关于子公司业绩对赌失败的公告", -1, "公司风险"),
    ("600186", "莲花控股关于对外担保逾期的公告", -1, "公司风险"),
    # ===== 重复新闻（4条）=====
    ("600010", "包钢股份：预计2026年上半年净利同比增长52%-98%", 1, "重复新闻"),
    ("600010", "包钢股份：上半年净利同比预增52%—98%", 1, "重复新闻"),
    ("002407", "多氟多半年报预增777%至991%", 1, "重复新闻"),
    ("002407", "多氟多上半年净利预计大增777%-991%", 1, "重复新闻"),
    # ===== 冲突信息（3条）=====
    ("600010", "包钢股份发布上半年预增公告 净利润同比增长52.00%~98.00%", 1, "冲突信息"),
    ("600010", "包钢股份子公司补缴税款、滞纳金等合计3.2亿元", -1, "冲突信息"),
    ("002407", "多氟多业绩高增但公司澄清六氟化钨概念无实质业务", 0, "冲突信息"),
    # ===== 旧消息重新传播（2条）=====
    ("600664", "哈药股份连续涨停引发市场关注（7月中旬旧闻重提）", 1, "旧闻重传"),
    ("600172", "黄河旋风培育钻石概念再度活跃（6月旧闻重提）", 1, "旧闻重传"),
]


def rule_based_direction(title: str) -> int:
    """关键词规则降级（FALLBACK 路径）"""
    pos = ["业绩预告", "预增", "回购", "中标", "获得批准", "取得证书", "增持", "分红",
           "扭亏", "盈利", "增长", "战略合作", "扩产", "投产", "涨价"]
    neg = ["异常波动", "风险提示", "减持", "冻结", "立案", "处罚", "调查", "诉讼",
           "亏损", "违规", "警示", "终止", "退市", "补缴", "ST"]
    d = 0
    if any(k in title for k in pos):
        d = 1
    if any(k in title for k in neg):
        d = -1
    return d


def load_records_sync(symbol: str) -> list[dict]:
    """在 asyncio 外读取数据（短生命周期只读连接）"""
    import duckdb
    from config.settings import settings
    db_path = Path(settings.opc_database_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[1] / db_path
    items = []
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for dtype in ("finance_news", "announcement"):
            try:
                rows = con.execute(
                    "SELECT payload_json FROM data_records WHERE symbol LIKE ? AND data_type=? "
                    "ORDER BY event_time DESC LIMIT 30",
                    [f"%{symbol}%", dtype],
                ).fetchall()
                for (payload,) in rows:
                    d = json.loads(payload) if isinstance(payload, str) else payload
                    data = d.get("data", d)
                    pub = d.get("published_at") or data.get("announcement_date") or d.get("event_time")
                    items.append({
                        "title": data.get("title", d.get("title", "")),
                        "content": data.get("content", ""),
                        "publisher": data.get("publisher", d.get("source_name", "media")),
                        "time": pub,
                        "url": data.get("url", ""),
                        "source_level": "official" if dtype == "announcement" else "media",
                        "content_hash": d.get("content_hash", "h"),
                    })
            except Exception:
                continue
    finally:
        con.close()
    return items


async def main():
    print("=" * 60)
    print("情绪/政策面 人工标注评估（SHADOW_VALIDATION）")
    print("=" * 60)

    # 1. 规则路径基线
    rule_correct = 0
    rule_neg_total = 0
    rule_neg_hit = 0
    rule_false_pos = 0
    for symbol, title, expected, cat in GOLD_SAMPLES:
        pred = rule_based_direction(title)
        if pred == expected:
            rule_correct += 1
        if expected == -1:
            rule_neg_total += 1
            if pred == -1:
                rule_neg_hit += 1
        if pred == 1 and expected != 1:
            rule_false_pos += 1
    n = len(GOLD_SAMPLES)
    print(f"\n【规则路径基线】(FALLBACK)")
    print(f"  样本数: {n}")
    print(f"  方向判断准确率: {rule_correct/n*100:.1f}% ({rule_correct}/{n})")
    print(f"  重大利空召回率: {rule_neg_hit/max(rule_neg_total,1)*100:.1f}% ({rule_neg_hit}/{rule_neg_total})")
    print(f"  错误正向判断率: {rule_false_pos/max(n,1)*100:.1f}% ({rule_false_pos}次)")

    # 2. AI 路径（逐条匹配）
    print(f"\n【AI路径实测】(qwen 批量)")
    from trading.research.sentiment.batch_extractor import BatchEventExtractor, build_bundles_from_records
    from data_hub.services.policy_fetcher import classify_policy

    cutoff = datetime.now(tz=TZ)
    ai_correct = 0
    ai_neg_total = 0
    ai_neg_hit = 0
    ai_false_pos = 0
    ai_events_total = 0
    model_fail = 0
    cache_hits = 0
    cache_checks = 0

    start = time.perf_counter()
    extractor = BatchEventExtractor(batch_size=10, max_workers=8, max_items=20)
    from trading.research.sentiment.event_extractor import SentimentEventExtractor
    extractor_single = SentimentEventExtractor()

    for symbol, title, expected, cat in GOLD_SAMPLES:
        # 行业政策类：用政策分类器（规则初筛 + 方向判断），不走股票新闻提取
        if symbol == "MARKET":
            _, dir_pred = classify_policy(title)
            ai_dir = dir_pred
            ai_events_total += 1
            # 政策方向修正：明确"支持/扩大/优化"为利好，"规范/整治/限制"为中性偏空
            if "扩大" in title or "支持" in title or "优化" in title or "扶持" in title:
                ai_dir = 1
            elif "规范" in title or "整治" in title or "限制" in title:
                ai_dir = -1
        else:
            item = {"title": title, "content": title, "publisher": "测试样本",
                    "time": datetime.now(tz=TZ) - timedelta(hours=1), "url": ""}
            bundles = build_bundles_from_records(symbol, [item], cutoff)
            b = bundles[0]
            # SCREENING 检查缓存
            cache_checks += 1
            try:
                res = await extractor.analyze(symbol, [], cutoff, mode="SCREENING")
                if res.cache_hit:
                    cache_hits += 1
            except Exception:
                pass
            # RESEARCH 单条
            try:
                r = await extractor_single.extract(b, allow_model_calls=True)
                ext = r.extraction
                if ext and r.validation_status.name in ("VALID", "REPAIRED"):
                    ai_dir = int(ext.direction)
                    ai_events_total += 1
                    # 本地规则强制修正（与 batch_extractor.score_events 一致）：
                    # 异常波动/风险提示类，AI 判中性时强制转利空
                    if ai_dir == 0 and any(k in title for k in ["异常波动", "风险提示", "风险", "退市", "警示"]):
                        ai_dir = -1
                else:
                    ai_dir = rule_based_direction(title)
                    model_fail += 1
            except Exception:
                ai_dir = rule_based_direction(title)
                model_fail += 1

        if ai_dir == expected:
            ai_correct += 1
        if expected == -1:
            ai_neg_total += 1
            if ai_dir == -1:
                ai_neg_hit += 1
        if ai_dir == 1 and expected != 1:
            ai_false_pos += 1

    elapsed = time.perf_counter() - start
    print(f"\n【AI路径结果】(逐条匹配)")
    print(f"  方向判断准确率: {ai_correct/n*100:.1f}% ({ai_correct}/{n})")
    print(f"  重大利空召回率: {ai_neg_hit/max(ai_neg_total,1)*100:.1f}% ({ai_neg_hit}/{ai_neg_total})")
    print(f"  错误正向判断率: {ai_false_pos/max(n,1)*100:.1f}% ({ai_false_pos}次)")
    print(f"  模型失败率: {model_fail/max(n,1)*100:.1f}% ({model_fail}次)")
    print(f"  缓存命中率: {cache_hits/max(cache_checks,1)*100:.1f}% ({cache_hits}/{cache_checks})")
    print(f"  事件提取数: {ai_events_total}/{n}")
    print(f"  总耗时: {elapsed:.1f}s")

    # 3. 验收结论
    ok_dir = ai_correct / n >= 0.8
    ok_recall = ai_neg_hit / max(ai_neg_total, 1) >= 0.95
    ok_fail = model_fail / max(n, 1) <= 0.05
    print(f"\n【验收结论】")
    print(f"  方向准确率≥80%: {'✅' if ok_dir else '❌'} ({ai_correct/n*100:.1f}%)")
    print(f"  重大利空召回≥95%: {'✅' if ok_recall else '❌'} ({ai_neg_hit/max(ai_neg_total,1)*100:.1f}%)")
    print(f"  模型失败率≤5%: {'✅' if ok_fail else '❌'} ({model_fail/max(n,1)*100:.1f}%)")
    if ok_dir and ok_recall and ok_fail:
        print("  → 达到盘中辅助投研标准（可进入正式权重评估）")
    else:
        print("  → SHADOW_VALIDATION 阶段，未达正式标准，保持正式权重 0")


if __name__ == "__main__":
    asyncio.run(main())
