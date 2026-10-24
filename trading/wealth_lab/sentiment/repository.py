"""Wealth Sentiment 独立数据层（database/wealth_sentiment.duckdb）。

与 wealth_lab.duckdb 完全隔离：只新建 wealth_news_* / wealth_event_* /
wealth_sentiment_* 表，绝不读写既有业务表。短连接 + 模块级写锁，
与既有 universe.py 的单写者模式一致。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.sentiment.config import SENTIMENT_DB_PATH

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_news_items (
    news_id VARCHAR PRIMARY KEY,
    source_provider VARCHAR NOT NULL,
    source_name VARCHAR NOT NULL,
    source_url VARCHAR,
    title VARCHAR NOT NULL,
    summary VARCHAR,
    published_at TIMESTAMPTZ,
    published_at_precision VARCHAR,
    first_seen_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    content_hash VARCHAR NOT NULL,
    language VARCHAR,
    asset_scope VARCHAR,
    raw_category VARCHAR,
    event_id VARCHAR,
    quality_status VARCHAR NOT NULL,
    ingest_version VARCHAR NOT NULL,
    raw_extra VARCHAR
);
CREATE TABLE IF NOT EXISTS wealth_news_events (
    event_id VARCHAR PRIMARY KEY,
    representative_news_id VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    published_at TIMESTAMPTZ,
    published_at_precision VARCHAR,
    first_seen_at TIMESTAMPTZ NOT NULL,
    raw_category VARCHAR,
    asset_scope VARCHAR,
    news_count INTEGER DEFAULT 1,
    source_count INTEGER DEFAULT 1,
    analysis_status VARCHAR DEFAULT 'PENDING',
    updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_event_analyses (
    analysis_id VARCHAR PRIMARY KEY,
    event_id VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    event_type VARCHAR,
    affected_asset_classes VARCHAR,
    affected_symbols VARCHAR,
    sentiment_direction VARCHAR,
    estimated_impact VARCHAR,
    confidence DOUBLE,
    evidence_spans VARCHAR,
    uncertainty VARCHAR,
    explanation VARCHAR,
    validation_flags VARCHAR,
    raw_output VARCHAR,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_sentiment_scores (
    score_id VARCHAR PRIMARY KEY,
    level VARCHAR NOT NULL,
    symbol VARCHAR,
    asof_at TIMESTAMPTZ NOT NULL,
    sentiment_score DOUBLE NOT NULL,
    confidence DOUBLE,
    coverage DOUBLE,
    observation_count INTEGER,
    source_count INTEGER,
    sentiment_version VARCHAR NOT NULL,
    evidence_ids VARCHAR,
    component_breakdown VARCHAR,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wealth_source_runs (
    run_id VARCHAR PRIMARY KEY,
    source_name VARCHAR NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status VARCHAR,
    items_fetched INTEGER,
    items_new INTEGER,
    items_duplicate INTEGER,
    items_skipped INTEGER,
    field_completeness DOUBLE,
    latest_published_at TIMESTAMPTZ,
    error VARCHAR,
    ingest_version VARCHAR
);
CREATE TABLE IF NOT EXISTS wealth_llm_calls (
    call_id VARCHAR PRIMARY KEY,
    agent VARCHAR NOT NULL,
    model VARCHAR,
    provider VARCHAR,
    prompt_version VARCHAR,
    prompt_sha256 VARCHAR,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    success BOOLEAN,
    error_type VARCHAR,
    created_at TIMESTAMPTZ
);
"""


def connect():
    con = duckdb.connect(str(SENTIMENT_DB_PATH))
    con.execute("SET TimeZone='Asia/Shanghai'")  # 展示时区统一 +08，底层仍为 UTC 瞬时值
    con.execute(SCHEMA)
    return con


def _now() -> datetime:
    return datetime.now(_TZ)


# ------------------------------------------------------------------ 资讯条目
def upsert_news_item(item: dict) -> bool:
    """幂等写入。返回 True=新条目，False=已存在（重复报道）。"""
    with _LOCK, connect() as con:
        exists = con.execute(
            "SELECT 1 FROM wealth_news_items WHERE news_id=?", [item["news_id"]]
        ).fetchone()
        if exists:
            # 关键词/关联线索合并（同一 URL 多关键词命中）
            row = con.execute(
                "SELECT asset_scope, raw_extra FROM wealth_news_items WHERE news_id=?",
                [item["news_id"]],
            ).fetchone()
            merged_scope = _json_union(row[0], item.get("asset_scope"))
            merged_extra = _json_union(row[1], item.get("raw_extra"))
            con.execute(
                "UPDATE wealth_news_items SET asset_scope=?, raw_extra=?, fetched_at=? WHERE news_id=?",
                [merged_scope, merged_extra, _now(), item["news_id"]],
            )
            return False
        con.execute(
            """INSERT INTO wealth_news_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [item.get(k) for k in (
                "news_id", "source_provider", "source_name", "source_url", "title",
                "summary", "published_at", "published_at_precision", "first_seen_at",
                "fetched_at", "content_hash", "language", "asset_scope", "raw_category",
                "event_id", "quality_status", "ingest_version", "raw_extra")],
        )
        return True


def _json_union(a: str | None, b: str | None) -> str:
    try:
        left = json.loads(a) if a else {}
    except Exception:
        left = {}
    try:
        right = json.loads(b) if b else {}
    except Exception:
        right = {}
    if isinstance(left, list) and isinstance(right, list):
        merged = list(dict.fromkeys(left + right))
    else:
        merged = {**left, **right}
    return json.dumps(merged, ensure_ascii=False)


# ------------------------------------------------------------------ 事件
def upsert_event(event: dict) -> str:
    """事件归并写入：按 event_id 幂等，重复报道计数 +1。返回 event_id。"""
    with _LOCK, connect() as con:
        exists = con.execute(
            "SELECT news_count, source_count, asset_scope FROM wealth_news_events WHERE event_id=?",
            [event["event_id"]],
        ).fetchone()
        if exists:
            news_count, source_count, scope_json = exists
            scope = _json_union(scope_json, event.get("asset_scope"))
            con.execute(
                """UPDATE wealth_news_events SET news_count=news_count+1,
                   source_count=?, asset_scope=?, updated_at=? WHERE event_id=?""",
                [max(source_count or 1, event.get("source_count", 1)),
                 scope, _now(), event["event_id"]],
            )
            return event["event_id"]
        con.execute(
            """INSERT INTO wealth_news_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [event.get(k) for k in (
                "event_id", "representative_news_id", "title", "published_at",
                "published_at_precision", "first_seen_at", "raw_category",
                "asset_scope", "news_count", "source_count", "analysis_status",
                "updated_at")],
        )
        return event["event_id"]


def link_item_to_event(news_id: str, event_id: str) -> None:
    with _LOCK, connect() as con:
        con.execute("UPDATE wealth_news_items SET event_id=? WHERE news_id=?",
                    [event_id, news_id])


def pending_events(limit: int) -> list[dict]:
    with _LOCK, connect() as con:
        rows = con.execute(
            """SELECT e.event_id, e.title, e.published_at, e.published_at_precision,
                      e.raw_category, e.asset_scope, e.news_count, e.source_count,
                      (SELECT string_agg(n.summary, ' || ' ORDER BY n.first_seen_at)
                       FROM wealth_news_items n WHERE n.event_id=e.event_id) AS summaries,
                      (SELECT string_agg(DISTINCT n.source_name, ',')
                       FROM wealth_news_items n WHERE n.event_id=e.event_id) AS sources
               FROM wealth_news_events e
               WHERE e.analysis_status='PENDING'
               ORDER BY (e.asset_scope NOT IN ('[]', '', 'null')) DESC,
                        e.published_at DESC NULLS LAST LIMIT ?""",
            [limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def set_event_analysis_status(event_id: str, status: str) -> None:
    with _LOCK, connect() as con:
        con.execute(
            "UPDATE wealth_news_events SET analysis_status=?, updated_at=? WHERE event_id=?",
            [status, _now(), event_id],
        )


# ------------------------------------------------------------------ 分析
def save_analysis(record: dict) -> None:
    with _LOCK, connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO wealth_event_analyses VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [record.get(k) for k in (
                "analysis_id", "event_id", "model_id", "provider", "prompt_version",
                "status", "event_type", "affected_asset_classes", "affected_symbols",
                "sentiment_direction", "estimated_impact", "confidence",
                "evidence_spans", "uncertainty", "explanation", "validation_flags",
                "raw_output", "input_tokens", "output_tokens", "latency_ms", "created_at")],
        )


def audit_llm_call(call_id: str, agent: str, model: str | None, provider: str | None,
                   prompt: str, prompt_version: str, meta: dict) -> None:
    import hashlib
    with _LOCK, connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO wealth_llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [call_id, agent, model, provider, prompt_version,
             hashlib.sha256((prompt_version + prompt).encode("utf-8")).hexdigest(),
             meta.get("input_tokens"), meta.get("output_tokens"), meta.get("latency_ms"),
             meta.get("success", False), meta.get("error_type"), _now()],
        )


# ------------------------------------------------------------------ 聚合输入与得分
def analyzed_events_since(since: datetime) -> list[dict]:
    """取窗口内已分析事件（含最新一次分析结果），供确定性聚合。"""
    with _LOCK, connect() as con:
        rows = con.execute(
            """SELECT e.event_id, e.title, e.published_at, e.raw_category,
                      e.asset_scope AS event_asset_scope, e.news_count, e.source_count,
                      a.analysis_id, a.event_type, a.affected_asset_classes,
                      a.affected_symbols, a.sentiment_direction, a.estimated_impact,
                      a.confidence, a.created_at AS analyzed_at
               FROM wealth_news_events e
               JOIN (
                   SELECT event_id, MAX(created_at) AS latest
                   FROM wealth_event_analyses WHERE status='VALID'
                   GROUP BY event_id
               ) m ON m.event_id=e.event_id AND m.latest=(
                   SELECT MAX(created_at) FROM wealth_event_analyses x
                   WHERE x.event_id=e.event_id AND x.status='VALID')
               JOIN wealth_event_analyses a ON a.event_id=e.event_id AND a.created_at=m.latest
               WHERE e.published_at IS NOT NULL AND e.published_at >= ?
               ORDER BY e.published_at DESC""",
            [since],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def save_score(row: dict) -> None:
    with _LOCK, connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO wealth_sentiment_scores VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [row.get(k) for k in (
                "score_id", "level", "symbol", "asof", "sentiment_score",
                "confidence", "coverage", "observation_count", "source_count",
                "sentiment_version", "evidence_ids", "component_breakdown", "created_at")],
        )


def save_source_run(run: dict) -> None:
    with _LOCK, connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO wealth_source_runs VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [run.get(k) for k in (
                "run_id", "source_name", "started_at", "finished_at", "status",
                "items_fetched", "items_new", "items_duplicate", "items_skipped",
                "field_completeness", "latest_published_at", "error", "ingest_version")],
        )


# ------------------------------------------------------------------ 查询（API 用）
def latest_scores(level: str, symbol: str | None = None, limit: int = 1) -> list[dict]:
    cond = "level=? AND symbol IS NOT DISTINCT FROM ?"
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT * FROM ({ _SCORE_SELECT}) WHERE {cond}
                ORDER BY "asof" DESC LIMIT ?""",
            [level, symbol, limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


_SCORE_SELECT = """SELECT score_id, level, symbol, asof_at AS asof, sentiment_score, confidence,
        coverage, observation_count, source_count, sentiment_version,
        evidence_ids, component_breakdown, created_at FROM wealth_sentiment_scores"""


def score_history(level: str, symbol: str | None, since: datetime, limit: int = 200) -> list[dict]:
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT * FROM ({_SCORE_SELECT})
                WHERE level=? AND symbol IS NOT DISTINCT FROM ? AND "asof" >= ?
                ORDER BY "asof" DESC LIMIT ?""",
            [level, symbol, since, limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def list_events(category: str | None = None, symbol: str | None = None,
                since: datetime | None = None, limit: int = 50,
                analysis_status: str | None = None) -> list[dict]:
    conds, args = [], []
    if category:
        conds.append("e.raw_category=?"); args.append(category)
    if analysis_status:
        conds.append("e.analysis_status=?"); args.append(analysis_status)
    if since:
        conds.append("e.published_at >= ?"); args.append(since)
    if symbol:
        conds.append("contains(e.asset_scope, ?)"); args.append(symbol)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT e.event_id, e.title, e.published_at, e.published_at_precision,
                       e.raw_category, e.asset_scope, e.news_count, e.source_count,
                       e.analysis_status, e.first_seen_at,
                       (SELECT a.sentiment_direction FROM wealth_event_analyses a
                        WHERE a.event_id=e.event_id AND a.status='VALID'
                        ORDER BY a.created_at DESC LIMIT 1) AS sentiment_direction,
                       (SELECT a.analysis_id FROM wealth_event_analyses a
                        WHERE a.event_id=e.event_id AND a.status='VALID'
                        ORDER BY a.created_at DESC LIMIT 1) AS analysis_id
                FROM wealth_news_events e {where}
                ORDER BY e.published_at DESC NULLS LAST LIMIT ?""",
            [*args, limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def asset_news(symbol: str, limit: int = 30) -> list[dict]:
    """指定资产关联资讯（事件级 + 条目级）。"""
    with _LOCK, connect() as con:
        rows = con.execute(
            """SELECT n.news_id, n.title, n.summary, n.source_name, n.source_url,
                      n.published_at, n.quality_status, n.event_id, n.raw_category,
                      n.first_seen_at
               FROM wealth_news_items n
               WHERE contains(n.asset_scope, ?) OR (
                   n.event_id IN (SELECT event_id FROM wealth_news_events
                                  WHERE contains(asset_scope, ?)))
               ORDER BY n.published_at DESC NULLS LAST LIMIT ?""",
            [symbol, symbol, limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def evidence_for_score(score_id: str) -> dict | None:
    with _LOCK, connect() as con:
        score = con.execute(
            f"SELECT * FROM ({_SCORE_SELECT}) WHERE score_id=?", [score_id]
        ).fetchone()
        if score is None:
            return None
        cols = [d[0] for d in con.description]
        score = dict(zip(cols, score))
        evidence_ids = json.loads(score.pop("evidence_ids") or "[]")
        analyses = []
        for aid in evidence_ids:
            row = con.execute(
                """SELECT a.analysis_id, a.event_id, a.model_id, a.provider,
                          a.prompt_version, a.status, a.event_type,
                          a.affected_asset_classes, a.affected_symbols,
                          a.sentiment_direction, a.estimated_impact, a.confidence,
                          a.evidence_spans, a.uncertainty, a.explanation,
                          a.validation_flags, a.created_at,
                          e.title, e.published_at, e.raw_category, e.news_count, e.source_count
                   FROM wealth_event_analyses a
                   LEFT JOIN wealth_news_events e USING (event_id)
                   WHERE a.analysis_id=?""",
                [aid],
            ).fetchone()
            if row:
                acols = [d[0] for d in con.description]
                analyses.append(dict(zip(acols, row)))
    score["analyses"] = analyses
    return score


def source_stats() -> list[dict]:
    with _LOCK, connect() as con:
        rows = con.execute(
            """SELECT source_name, COUNT(*) AS runs,
                      SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) AS ok_runs,
                      MAX(finished_at) AS last_run_at,
                      AVG(field_completeness) AS avg_field_completeness,
                      SUM(items_new) AS total_new,
                      MAX(latest_published_at) AS latest_published_at
               FROM wealth_source_runs GROUP BY source_name ORDER BY source_name"""
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]
