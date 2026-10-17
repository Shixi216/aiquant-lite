# ruff: noqa: E402
# Standalone CLI bootstrap configures paths or runtime before project imports.
# -*- coding: utf-8 -*-
"""
单只票一键深度分析（正式工具，2026-09-09 固化）
用法: .venv/Scripts/python.exe scripts/analyze_one.py 600354.SH [--no-xcheck] [--days 90]
链路: 调 Router /v1/research/stock-report(标准五维接口) 拿完整报告 -> 可选跑 cross_validate.py 交叉验证
      -> 落盘 reports/screening/analyze_one_<code>.json
这是"单只票五维分析"的统一入口, 禁止每次临时写脚本调 Router。
"""

import sys
import os
import time
import json
import subprocess
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ["NO_PROXY"] = "*"
import httpx


def retry(fn, n=3):
    for i in range(n):
        try:
            return fn()
        except Exception:
            time.sleep(3)
    return None


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/analyze_one.py <代码> [--no-xcheck] [--days N]")
        sys.exit(1)
    code = sys.argv[1]
    if "." not in code:
        # 6位数字补后缀
        code = code + ".SH" if code.startswith(("6", "9")) else code + ".SZ"
    no_xcheck = "--no-xcheck" in sys.argv
    use_ai = "--ai" in sys.argv  # 默认 no_ai=True(快,关键词情绪)；加 --ai 启用 qwen 情绪提取
    days = 90
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    # 1. Router 五维完整报告
    with httpx.Client(trust_env=False, timeout=180) as cli:
        r = retry(
            lambda: cli.post(
                "http://127.0.0.1:8765/v1/research/stock-report",
                json={
                    "symbol": code,
                    "days": days,
                    "no_fetch": False,
                    "no_ai": not use_ai,
                    "force_refresh": True,
                },
            )
        )
        if r is None or r.status_code != 200:
            print(f"Router 返回 {r.status_code if r else 'None'}, 检查 Router 健康(8765)")
            sys.exit(1)
        try:
            rep = r.json().get("report", "")
        except Exception:
            rep = r.text
    print("=" * 60)
    print(rep)
    print("=" * 60)

    # 1.5 增发信息(2026-09-17 接入 stk_seasoned，补事件面/政策面覆盖)
    issue_txt = ""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import stock_issue

        issue = stock_issue.fetch_issue(code, limit=5)
        issue_txt = stock_issue.render(issue, code)
    except Exception as e:
        issue_txt = f"⚠️ 增发查询失败: {str(e)[:60]}"
    print("=" * 60)
    print(issue_txt)
    print("=" * 60)

    # 2. 落盘
    output_dir = Path(__file__).resolve().parents[1] / "reports" / "screening"
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"analyze_one_{code[:6]}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {"code": code, "report": rep, "stock_issue": issue_txt}, f, ensure_ascii=False, indent=1
        )
    print(f"\n报告已落盘: {out}")

    # 3. 交叉验证
    if not no_xcheck:
        print("\n=== 交叉验证(近10日K线/龙虎榜/主力资金/矛盾信号) ===")
        xcheck = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cross_validate.py")
        subprocess.run([sys.executable, xcheck, code])


if __name__ == "__main__":
    main()
