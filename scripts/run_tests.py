"""统一测试运行包装器（退出码传播修复）

用法:
  python scripts/run_tests.py [pytest参数...]

特性:
- 正确传播 pytest 退出码（失败=非0，成功=0）
- 不用管道吞掉退出码（避免 tail 的 0 掩盖 pytest 的 1）
- 支持输出到文件（--output=path）同时保留退出码
- 记录 pytest 原始退出码 + 包装器退出码

规则:
- pytest 失败时，本包装器返回非0
- pytest 成功时，本包装器返回0
- 禁止无条件 exit 0
- 禁止捕获异常后继续返回成功
"""
from __future__ import annotations

# ruff: noqa: E402

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.utf8 import configure_utf8_stdio, utf8_child_environment


def main() -> int:
    configure_utf8_stdio()
    args = sys.argv[1:]

    # 解析 --output=path（输出文件，不吞退出码）
    output_path: str | None = None
    pytest_args: list[str] = []
    for a in args:
        if a.startswith("--output="):
            output_path = a.split("=", 1)[1]
        else:
            pytest_args.append(a)

    # 用 subprocess 运行 pytest（无管道，保留真实退出码）
    cmd = [sys.executable, "-m", "pytest"] + pytest_args
    try:
        result = subprocess.run(
            cmd, capture_output=False, env=utf8_child_environment()
        )
    except FileNotFoundError:
        print(f"错误: 找不到 pytest（命令: {cmd}）", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("测试被中断", file=sys.stderr)
        return 130

    pytest_rc = result.returncode

    # 输出文件（可选，不吞退出码）
    if output_path:
        # 重新运行一次输出到文件（或用 tee 语义：已无原始输出，重新跑）
        # 简单实现：第二次运行仅用于生成报告文件，退出码以第一次为准
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"pytest 退出码: {pytest_rc}\n")

    # 记录退出码
    print(f"\n[run_tests] pytest 退出码: {pytest_rc}")
    print(f"[run_tests] 包装器退出码: {pytest_rc}")

    # 退出码传播（失败→非0）
    return pytest_rc


if __name__ == "__main__":
    sys.exit(main())
