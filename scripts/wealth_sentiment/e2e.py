# -*- coding: utf-8 -*-
"""Wealth Sentiment v1 端到端脚本（与任务控制中心同一 service 层，真实执行）。

用法：
  python scripts/wealth_sentiment/e2e.py refresh [源名,源名]   # 真实采集（缺省=全部启用源）
  python scripts/wealth_sentiment/e2e.py analyze [N]           # LLM 批量分析 N 个事件（默认 8）
  python scripts/wealth_sentiment/e2e.py factor [lookback_h]   # 确定性聚合（默认 168h）
  python scripts/wealth_sentiment/e2e.py status                # 总体状态统计
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trading.wealth_lab.sentiment import service as svc  # noqa: E402


def _progress(msg: str) -> bool:
    print("  ·", msg, flush=True)
    return True


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "refresh":
        sources = sys.argv[2].split(",") if len(sys.argv) > 2 else None
        result = svc.refresh_news(progress=_progress, sources=sources)
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    elif cmd == "analyze":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
        result = svc.analyze_sentiment(progress=_progress, limit=limit)
        print(json.dumps({k: v for k, v in result.items() if k != "results"},
                         ensure_ascii=False, indent=1))
        for r in result["results"]:
            print(f"  {r.get('status'):20s} {r.get('direction') or r.get('error','')[:60]} "
                  f"[{r.get('provider')}/{r.get('model')}] {r.get('event_id','')[:20]}")
    elif cmd == "factor":
        lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 168
        print(json.dumps(svc.rebuild_factor(progress=_progress, lookback_hours=lookback),
                         ensure_ascii=False, indent=1))
    elif cmd == "status":
        from trading.wealth_lab.sentiment import repository as repo
        with repo.connect() as con:
            print("news_items:", con.execute("SELECT COUNT(*) FROM wealth_news_items").fetchone()[0])
            print("events by status:",
                  con.execute("SELECT analysis_status, COUNT(*) FROM wealth_news_events "
                              "GROUP BY 1 ORDER BY 2 DESC").fetchall())
            print("llm calls:", con.execute("SELECT success, COUNT(*) FROM wealth_llm_calls "
                                            "GROUP BY 1").fetchall())
            market = repo.latest_scores("MARKET", None, 1)
            print("market score:", market[0]["sentiment_score"] if market else None,
                  "| obs:", market[0]["observation_count"] if market else 0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
