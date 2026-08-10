"""端到端耗时测量（2026-08 任务四）— 每项10次，min/P50/P95/max

测量项：
1. 内存快照读取耗时
2. Scanner业务服务耗时
3. FastAPI接口端到端耗时
4. Hermes Agent编排耗时（模拟：网关调用）
5. 企业微信从发问到收到回复（模拟：网关+格式化）
6. QQ端（模拟：网关+格式化）
7. Web从点击到渲染（模拟：FastAPI+格式化）
8. 阶段2研究后总耗时
9. DECISION后总耗时
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure(fn, n: int = 10) -> dict:
    """测量 n 次，返回 min/p50/p95/max（毫秒）"""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95) - 1] if len(samples) > 1 else samples[-1]
    return {
        "min_ms": round(samples[0], 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(samples[-1], 1),
        "samples": len(samples),
    }


def main() -> None:
    results = {}

    # 1. 内存快照读取
    from data_hub.services.market_realtime_snapshot import MarketRealtimeSnapshotService
    svc = MarketRealtimeSnapshotService()
    # 确保快照就绪（若STALE或空则先刷新一次；热路径测量的是已有快照读取）
    quotes, meta = svc.get_snapshot()
    if not quotes or meta["status"] == "STALE_REALTIME":
        svc.refresh_once()
        print(f"[预热] 快照刷新完成: {meta['status']} → {svc.get_snapshot()[1]['status']}")

    def _snapshot_read():
        svc.get_snapshot()
    results["1_内存快照读取"] = measure(_snapshot_read, 10)

    # 2. Scanner 业务服务（阶段1候选生成）
    from scripts.intraday_pipeline import IntradayPipeline
    pipe = IntradayPipeline()

    def _stage1():
        pipe.stage1_candidates(max_candidates=50)
    results["2_阶段1候选生成"] = measure(_stage1, 10)

    # 3. FastAPI 接口端到端
    from fastapi.testclient import TestClient
    from data_hub.api.app import app
    client = TestClient(app)

    def _api_scan():
        client.post("/v1/scan", json={"query": "今天有什么好票", "channel": "web",
                                      "max_research": 3})
    results["3_FastAPI接口端到端"] = measure(_api_scan, 10)

    # 4. Hermes Agent 编排（网关调用）
    from trading.scanner.unified_gateway import ScanRequest, UnifiedScanGateway
    gw = UnifiedScanGateway()

    def _gateway():
        gw.scan(ScanRequest(query="今天有什么好票", channel="wecom", max_research=3))
    results["4_网关编排"] = measure(_gateway, 10)

    # 5. 企微/6.QQ/7.Web（同一网关，仅 channel 不同）
    for ch in ["wecom", "qq", "web"]:
        def _ch(ch=ch):
            gw.scan(ScanRequest(query="今天有什么好票", channel=ch, max_research=3))
        results[f"5_{ch}_入口"] = measure(_ch, 10)

    # 8. 阶段2研究后总耗时（分别报告不同工作量）
    def _research_n(n):
        def fn():
            pipe.run(max_research=n, verbose=False)
        return fn
    results["8a_阶段2研究1只"] = measure(_research_n(1), 5)
    results["8b_阶段2研究5只"] = measure(_research_n(5), 5)
    results["8c_阶段2研究10只"] = measure(_research_n(10), 5)

    # 9. DECISION 后总耗时（决策引擎）
    from trading.decision_support.decision_engine import DecisionEngine, DecisionInput
    from trading.decision_support.task_context import TaskContext
    from trading.decision_support.data_status import DataStatus

    def _decision():
        ctx = TaskContext(local_user_id="u1", external_user_id="wx1", symbol="002415")
        inp = DecisionInput(task=ctx, formal_score=0.5, technical_score=0.6,
                            fundamental_score=0.4, confidence=0.7,
                            data_status=DataStatus.FRESH, current_price=38.0, bars=[])
        DecisionEngine().decide(inp)
    results["9_DECISION后总耗时"] = measure(_decision, 10)

    # 输出
    print("=== 端到端耗时（10次，毫秒）===")
    for name, r in results.items():
        print(f"{name}: min={r['min_ms']} P50={r['p50_ms']} P95={r['p95_ms']} max={r['max_ms']}")

    # 目标：读取已有实时快照返回阶段1候选 ≤3秒（3000ms）
    stage1 = results["2_阶段1候选生成"]
    target_ok = stage1["p95_ms"] <= 3000
    print(f"\n阶段1候选 P95 {stage1['p95_ms']}ms ≤ 3000ms: {'✅' if target_ok else '❌'}")
    print(f"内存快照读取 P50 {results['1_内存快照读取']['p50_ms']}ms（目标毫秒级）")


if __name__ == "__main__":
    main()
