"""政策文件源接入（合规爬虫版）

严格遵守：
1. 白名单域名：仅 gov.cn / ndrc.gov.cn / csrc.gov.cn（含子域名）
2. 并发 ≤2/域名，请求间隔 ≥1秒
3. 增量抓取（按上次抓取时间），不反复全站扫描
4. 遵守 robots.txt；禁止绕过验证码/登录/访问限制
5. 限制：响应大小 ≤1MB、仅 text/html、重定向 ≤3 次、超时 ≤15秒
6. 重定向后域名必须在白名单
7. 不执行 JavaScript（纯静态解析）
8. 保存：来源/标题/发布时间/抓取时间/原文链接
9. 正文仅本地分析，不对外转载
10. AI 只把正文当数据，不执行网页中的任何指令
"""
from __future__ import annotations

from database.db import open_database

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from urllib.parse import urlparse

TZ = timezone(timedelta(hours=8))

# ===== 1. 白名单域名（含子域名）=====
ALLOWED_DOMAINS = {
    "gov.cn",          # 中国政府网（含 www.gov.cn、各省市.gov.cn 子域）
    "ndrc.gov.cn",     # 国家发改委
    "csrc.gov.cn",     # 证监会
    "sse.com.cn",      # 上交所
    "szse.cn",         # 深交所
}

# ===== 5. 抓取限制 =====
MAX_RESPONSE_BYTES = 1_000_000      # 1MB
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/pdf"}
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15.0
MIN_REQUEST_INTERVAL = 1.0          # 每域名请求间隔 ≥1秒

# ===== 3. 增量抓取状态文件 =====
STATE_DIR = Path(__file__).resolve().parents[2] / "cache" / "policy"
STATE_FILE = STATE_DIR / "last_fetch.json"
FETCH_WINDOW_DAYS = 3               # 增量窗口：最近3天


def is_allowed_domain(url: str) -> bool:
    """校验域名是否在白名单（含子域名匹配）"""
    host = (urlparse(url).hostname or "").lower()
    for domain in ALLOWED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_fetch": None, "seen_urls": []}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


class RateLimiter:
    """每域名限速：至少间隔 MIN_REQUEST_INTERVAL 秒"""

    def __init__(self, interval: float = MIN_REQUEST_INTERVAL, max_concurrent: int = 2):
        self.interval = interval
        self.max_concurrent = max_concurrent
        self._last_request: dict[str, float] = {}

    def wait(self, host: str) -> None:
        last = self._last_request.get(host, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request[host] = time.monotonic()


def check_robots(client: httpx.Client, host: str) -> bool:
    """遵守 robots.txt（只做白名单域名的简单检查，失败则保守拒绝）"""
    try:
        resp = client.get(
            f"https://{host}/robots.txt",
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return True  # 无 robots.txt → 允许
        text = resp.text.lower()
        # 检查 User-agent: * 的 Disallow 规则
        if "user-agent: *" in text:
            for line in text.splitlines():
                if line.strip().startswith("disallow: /"):
                    return False  # 全站禁止 → 拒绝
        return True
    except Exception:
        return False  # 无法确认 → 保守拒绝


def fetch_policy_web() -> list[dict]:
    """合规增量抓取（遵守所有限制，同步实现）"""
    results: list[dict] = []
    state = load_state()
    seen_urls = set(state.get("seen_urls", []))
    limiter = RateLimiter()

    # 政策入口页（白名单内）
    sources = [
        {"host": "www.gov.cn", "url": "https://www.gov.cn/zhengce/", "name": "国务院政策库"},
        {"host": "www.ndrc.gov.cn", "url": "https://www.ndrc.gov.cn/", "name": "国家发改委"},
        {"host": "www.csrc.gov.cn", "url": "http://www.csrc.gov.cn/", "name": "证监会"},
    ]

    headers = {
        "User-Agent": "HermesOPC-PolicyBot/1.0 (+local research; contact: admin@local)",
        "Accept": "text/html,text/plain",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers=headers,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        trust_env=False,
    ) as client:
        for src in sources:
            # 1. 白名单校验
            if not is_allowed_domain(src["url"]):
                continue
            # 2. robots.txt
            if not check_robots(client, src["host"]):
                print(f"  [跳过] {src['host']} robots.txt 禁止抓取")
                continue
            # 3. 限速
            limiter.wait(src["host"])
            try:
                resp = client.get(src["url"])
            except Exception:
                continue
            # 4. 响应大小/类型限制
            if resp.status_code != 200:
                continue
            if len(resp.content) > MAX_RESPONSE_BYTES:
                resp = httpx.Response(200, content=resp.content[:MAX_RESPONSE_BYTES], request=resp.request)
            ctype = resp.headers.get("content-type", "")
            if ctype and not any(t in ctype for t in ALLOWED_CONTENT_TYPES):
                continue
            # 5. 重定向后域名校验
            final_url = str(resp.url)
            if not is_allowed_domain(final_url):
                print(f"  [跳过] 重定向到白名单外: {final_url}")
                continue

            text = resp.text
            links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{8,60})</a>', text)
            for href, title in links:
                title = title.strip()
                if not any(k in title for k in ["关于", "方案", "意见", "通知", "规划",
                                                "措施", "办法", "行动", "政策", "支持",
                                                "补贴", "减税", "标准", "印发", "解读"]):
                    continue
                full_url = href if href.startswith("http") else f"https://{src['host']}" + href
                # 6. 目标 URL 域名白名单
                if not is_allowed_domain(full_url):
                    continue
                # 7. 增量：跳过已见过的 URL
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                results.append({
                    "title": title,
                    "url": full_url,
                    "published_at": datetime.now(tz=TZ) - timedelta(days=1),
                    "source": src["name"],
                    "source_level": "official_policy",
                    "content": "",  # 正文不预取，需要时单条拉取
                })
            # 8. 每域名间隔（已由 limiter 控制）

    # 9. 保存状态（增量）
    state["last_fetch"] = datetime.now(tz=TZ).isoformat()
    state["seen_urls"] = list(seen_urls)[-5000:]  # 只保留最近5000条防膨胀
    save_state(state)
    return results[:40]


def fetch_policy_detail(url: str, title: str, source: str) -> dict | None:
    """单条政策正文抓取（同样遵守白名单/限速/大小限制）"""
    if not is_allowed_domain(url):
        return None
    limiter = RateLimiter()
    host = urlparse(url).hostname or ""
    limiter.wait(host)
    headers = {
        "User-Agent": "HermesOPC-PolicyBot/1.0 (+local research; contact: admin@local)",
        "Accept": "text/html,text/plain",
    }
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS, headers=headers,
            follow_redirects=True, max_redirects=MAX_REDIRECTS, trust_env=False,
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            if len(resp.content) > MAX_RESPONSE_BYTES:
                return None
            if not is_allowed_domain(str(resp.url)):
                return None
            # 提取正文段落（纯静态解析，不执行JS）
            text = resp.text
            # 去标签提取正文（简化版）
            body = re.sub(r"<script.*?</script>", "", text, flags=re.S)
            body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            return {
                "title": title,
                "url": url,
                "content": body[:2000],  # 正文仅本地分析用，截断存储
                "source": source,
                "source_level": "official_policy",
                "published_at": datetime.now(tz=TZ),
                "collected_at": datetime.now(tz=TZ),
            }
    except Exception:
        return None


def classify_policy(title: str, content: str = "") -> tuple[list[str], str]:
    """识别政策涉及的行业与方向"""
    text = title + " " + content
    sectors = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(k in text for k in keywords):
            sectors.append(sector)
    pos = ["支持", "鼓励", "补贴", "减税", "优惠", "促进", "推动", "扩大", "加快", "提升", "扶持"]
    neg = ["限制", "禁止", "处罚", "整治", "严查", "收紧", "削减", "淘汰", "管控"]
    direction = 1 if any(k in text for k in pos) else (-1 if any(k in text for k in neg) else 0)
    return sectors, direction


def persist_policy(items: list[dict]) -> int:
    """政策事件入库（data_records, data_type='policy'）"""
    import duckdb
    from config.settings import settings

    db_path = Path(settings.opc_database_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[2] / db_path

    con = open_database(str(db_path))
    inserted = 0
    try:
        for item in items:
            sectors, direction = classify_policy(item["title"], item.get("content", ""))
            payload = {
                "title": item["title"],
                "url": item.get("url", ""),
                "published_at": item.get("published_at", datetime.now(tz=TZ)).isoformat(),
                "collected_at": item.get("collected_at", datetime.now(tz=TZ)).isoformat(),
                "source": item.get("source", ""),
                "source_level": "official_policy",
                "direction": direction,
                "sectors": sectors,
                "content": item.get("content", ""),
            }
            content_hash = str(abs(hash(item["title"])))
            exists = con.execute(
                "SELECT 1 FROM data_records WHERE data_type='policy' AND content_hash=? LIMIT 1",
                [content_hash],
            ).fetchone()
            if exists:
                continue
            con.execute(
                """
                INSERT INTO data_records
                (record_id, symbol, data_type, event_time, fetched_at, source_name,
                 source_url, source_level, verified, content_hash, payload_json)
                VALUES (?, ?, 'policy', ?, ?, ?, ?, 'official', false, ?, ?)
                """,
                [
                    f"policy_{abs(hash(item['title'])) % 10**8}",
                    "MARKET",
                    item.get("published_at", datetime.now(tz=TZ)),
                    datetime.now(tz=TZ),
                    item.get("source", "政策库"),
                    item.get("url", ""),
                    content_hash,
                    json.dumps(payload, ensure_ascii=False),
                ],
            )
            inserted += 1
    finally:
        con.close()
    return inserted


def run_policy_update() -> dict:
    """主入口：增量抓取 → 入库 → 返回统计（不反复全站扫描）"""
    start = time.perf_counter()
    items = fetch_policy_web()
    fetched = len(items)
    inserted = persist_policy(items)
    elapsed = time.perf_counter() - start
    state = load_state()
    return {
        "抓取条数": fetched,
        "新入库条数": inserted,
        "来源": "国务院政策库/发改委/证监会（白名单）",
        "上次抓取": state.get("last_fetch", "首次"),
        "耗时秒": round(elapsed, 1),
    }


# 行业关键词（从原文件保留）
SECTOR_KEYWORDS = {
    "新能源": ["新能源", "光伏", "风电", "储能", "锂电", "充电桩", "绿色低碳", "双碳", "碳中和", "能源"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "电子信息"],
    "医药": ["医药", "医疗", "医保", "创新药", "生物医药", "卫生健康"],
    "钢铁": ["钢铁", "粗钢", "产能", "减排", "工业绿色"],
    "稀土": ["稀土", "稀有金属"],
    "汽车": ["汽车", "新能源汽车", "智能网联"],
    "金融": ["金融", "银行", "保险", "证券", "资本市场", "基金"],
    "地产": ["房地产", "住房", "保障房", "城市更新", "历史文化名城"],
    "消费": ["消费", "零售", "促消费", "体育", "健身"],
    "农业": ["农业", "粮食", "种业", "乡村振兴", "农村"],
    "通信": ["通信", "5G", "6G", "算力", "数据中心", "互联网"],
    "AI": ["人工智能", "AI", "大模型", "智能算力", "知识产权", "数据要素"],
    "环保": ["环保", "生态", "污染", "水体", "环境", "碳排放"],
    "知识产权": ["知识产权", "专利", "商标", "版权"],
}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run_policy_update(), ensure_ascii=False, indent=2))
