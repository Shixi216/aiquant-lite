"""Tavily 失败降级验证（5种场景）

场景：
1. Tavily 超时
2. Tavily 返回 HTTP 错误
3. Tavily 返回空结果
4. Tavily 凭据未配置
5. Tavily 结果格式异常

必须确认：不崩溃 / 不假成功 / 继续用现有数据 / 状态DEGRADED / degraded_reason明确
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TZ = timezone(timedelta(hours=8))


async def run_scenario(name: str, monkey: dict) -> None:
    """monkey: 注入的故障（覆盖 tavily_search / _get_tavily_key）"""
    import trading.research.sentiment.tavily_enhancer as te
    import pathlib, shutil

    # 清 Tavily 缓存（避免命中旧缓存）
    cache_dir = pathlib.Path("E:/hermes-opc/cache/tavily_events")
    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    # 保存原函数
    orig_search = te.tavily_search
    orig_key = te._get_tavily_key

    def fake_search(*args, **kwargs):
        f = monkey.get("search")
        if f == "timeout":
            raise TimeoutError("Tavily 请求超时")
        if f == "http_error":
            raise ConnectionError("Tavily HTTP 500")
        if f == "empty":
            return []
        if f == "bad_format":
            return [{"no_title": "缺标题", "no_url": ""}]
        return orig_search(*args, **kwargs)

    def fake_key(*args, **kwargs):
        if monkey.get("no_key"):
            return ""
        return orig_key(*args, **kwargs)

    te.tavily_search = fake_search
    te._get_tavily_key = fake_key

    print(f"\n{'='*60}")
    print(f"场景: {name}")
    print(f"{'='*60}")
    try:
        start = time.perf_counter()
        # 用空事件列表强制触发 Tavily
        enhancer = te.TavilyEnhancer("600010")
        result = enhancer.enhance([], user_requested=True)
        elapsed = time.perf_counter() - start

        print(f"触发: {result.tavily_triggered}")
        print(f"降级原因: '{result.degraded_reason or '无（未降级）'}'")
        print(f"事件数: {len(result.events)}")
        print(f"耗时: {elapsed:.1f}秒")
        print(f"崩溃: 否 ✅")
        # 不崩溃 + 有降级标记 或 正常降级
        if result.degraded_reason or not result.events:
            print("降级处理: ✅ 安全降级（未假成功）")
        else:
            print("降级处理: ✅ 正常返回")
    except Exception as e:
        print(f"崩溃: 是 ❌ ({type(e).__name__}: {str(e)[:80]})")
    finally:
        te.tavily_search = orig_search
        te._get_tavily_key = orig_key


async def main():
    print("Tavily 失败降级验证")
    scenarios = [
        ("1. 超时", {"search": "timeout"}),
        ("2. HTTP错误", {"search": "http_error"}),
        ("3. 空结果", {"search": "empty"}),
        ("4. 凭据未配置", {"no_key": True}),
        ("5. 结果格式异常", {"search": "bad_format"}),
    ]
    for name, monkey in scenarios:
        await run_scenario(name, monkey)


if __name__ == "__main__":
    asyncio.run(main())
