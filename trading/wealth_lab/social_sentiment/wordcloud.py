"""确定性词频（词云数据源）：真实评论规则切分统计，不经 LLM 生成频率。

- 内置金融词典与文本片段规则；过滤停用词/纯数字/单字/URL/广告标记；
- 同批同文已由 content_hash 去重，另对同一词条在单条评论内只计一次
  （防刷屏重复灌水）；
- 金融主题词频按 FINANCE_TOPIC_TERMS 词典确定性归组；
- 输出附样本量与来源授权状态。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from trading.wealth_lab.social_sentiment import repository as repo
from trading.wealth_lab.social_sentiment.config import (
    FINANCE_TOPIC_TERMS, STOPWORDS_ZH,
)

_TZ = timezone(__import__("datetime").timedelta(hours=8))
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_REPEAT_RE = re.compile(r"(.)\1{2,}")  # 3 连以上同字符视为灌水噪声
_ASCII_TOKEN_RE = re.compile(r"[a-z][a-z0-9.+-]*", re.IGNORECASE)
_CJK_BLOCK_RE = re.compile(r"[\u3400-\u9fff]+")
_NOISE_MARKERS = ("加群", "微信", "vx", "免费领")
_FINANCE_TERMS = tuple(
    sorted({term.lower() for term, _ in FINANCE_TOPIC_TERMS}, key=lambda item: (-len(item), item))
)
_PHRASE_STOPWORDS = tuple(
    sorted((word for word in STOPWORDS_ZH if len(word) >= 2), key=lambda item: (-len(item), item))
)


def _tokens(text: str) -> list[str]:
    """使用项目内规则切分文本，避免未声明的运行时分词依赖。"""
    cleaned = _URL_RE.sub(" ", text or "").lower()
    cleaned = _REPEAT_RE.sub(r"\1", cleaned)
    candidates: list[str] = _ASCII_TOKEN_RE.findall(cleaned)

    # 金融术语直接按内置词典提取，保证“黄金/定投/QDII”等核心语义稳定。
    candidates.extend(term for term in _FINANCE_TERMS if term in cleaned)

    # 保留未被金融词典或多字停用词覆盖的中文片段，用于展示评论中的新话题。
    for block in _CJK_BLOCK_RE.findall(cleaned):
        residual = block
        for term in (*_FINANCE_TERMS, *_PHRASE_STOPWORDS):
            residual = residual.replace(term, " ")
        candidates.extend(_CJK_BLOCK_RE.findall(residual))

    out: list[str] = []
    for token in candidates:
        token = token.strip().lower()
        if len(token) < 2 or token in STOPWORDS_ZH:
            continue
        if token.isdigit() or re.fullmatch(r"[\W_]+", token):
            continue
        if any(marker in token for marker in _NOISE_MARKERS):
            continue
        out.append(token)
    return out


def word_frequency(symbol: str | None = None, platform: str | None = None,
                   top_k: int = 50, include_synthetic: bool = False) -> dict:
    """评论词频 + 金融主题词频（确定性）。无真实数据时返回 INSUFFICIENT_DATA。"""
    comments = repo.all_comments(include_synthetic=include_synthetic)
    if symbol:
        comments = [c for c in comments if c.get("symbol") == symbol]
    if platform:
        comments = [c for c in comments if c["platform"] == platform]
    comments = [c for c in comments if c["data_origin"] != "synthetic_test"
                or include_synthetic]
    if not comments:
        return {"status": "INSUFFICIENT_DATA", "sample_size": 0,
                "message": "无真实、获准使用的评论（词云不由 LLM 生成频率）"}

    freq: Counter = Counter()
    topic_freq: Counter = Counter()
    topic_map = {term.lower(): topic for term, topic in FINANCE_TOPIC_TERMS}
    for comment in comments:
        # 排序后更新，确保相同频次时的输出顺序不受哈希随机化影响。
        tokens = sorted(set(_tokens(comment["content"])))
        freq.update(tokens)
        for token in tokens:
            topic = topic_map.get(token)
            if topic:
                topic_freq[topic] += 1
        # 多字主题词直接子串匹配（"降息"等常被切成单字上下文时兜底）
        for term, topic in FINANCE_TOPIC_TERMS:
            if term.lower() in (comment["content"] or "").lower():
                topic_freq[topic] += 1

    from trading.wealth_lab.social_sentiment import registry
    source_ids = {c["source_id"] for c in comments}
    statuses = {s["source_id"]: s["status"] for s in registry.list_sources()}
    return {
        "status": "OK",
        "asof": datetime.now(_TZ).isoformat(),
        "symbol": symbol, "platform": platform,
        "word_frequency": freq.most_common(top_k),
        "finance_topic_frequency": topic_freq.most_common(),
        "sample_size": len(comments),
        "distinct_terms": len(freq),
        "source_authorization": {sid: statuses.get(sid, "UNKNOWN")
                                 for sid in sorted(source_ids)},
        "note": "词频为确定性规则统计（内置金融词典 + 停用词/广告过滤），非 LLM 生成",
    }
