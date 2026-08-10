"""FastAPI 常驻性能测试（任务书 2026-08 规则7）

- 服务启动导入耗时（含所有重型库）
- 第一次业务调用耗时
- 第二次热调用耗时（目标 ≤1 秒）

用 TestClient 模拟常驻进程（import 一次，多请求复用）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    # 1. 服务启动导入耗时（首次 import 全部重型库）
    t0 = time.perf_counter()
    from data_hub.api.app import app  # noqa: F401
    t_import = time.perf_counter() - t0
    print(f"① 服务启动导入耗时: {t_import:.2f}秒")

    # 2. 第一次业务调用（含路由初始化）
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        t1 = time.perf_counter()
        r1 = client.get("/health")
        t_first = time.perf_counter() - t1
        print(f"② 第一次业务调用(/health): {t_first:.3f}秒 | 状态: {r1.status_code}")

        # 3. 第二次热调用
        t2 = time.perf_counter()
        r2 = client.get("/health")
        t_hot = time.perf_counter() - t2
        print(f"③ 第二次热调用(/health): {t_hot:.3f}秒 | 状态: {r2.status_code}")

        # 4. capabilities 接口（业务接口）
        t3 = time.perf_counter()
        r3 = client.get("/v1/capabilities")
        t_cap = time.perf_counter() - t3
        print(f"④ capabilities接口: {t_cap:.3f}秒 | 状态: {r3.status_code}")

        # 5. 再次热调用 capabilities
        t4 = time.perf_counter()
        r4 = client.get("/v1/capabilities")
        t_cap2 = time.perf_counter() - t4
        print(f"⑤ capabilities热调用: {t_cap2:.3f}秒")

    print("\n=== 结果 ===")
    hot_ok = t_hot <= 1.0 and t_cap2 <= 1.0
    print(f"热调用 ≤1秒: {'✅ 达标' if hot_ok else '❌ 未达标'}")
    print(f"   /health: {t_hot:.3f}s | capabilities: {t_cap2:.3f}s")
    print(f"首次调用: {t_first:.3f}s（冷启动，允许较慢）")


if __name__ == "__main__":
    main()
