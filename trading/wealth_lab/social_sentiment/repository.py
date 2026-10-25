"""Social Sentiment 独立数据层（database/wealth_social_sentiment.duckdb）。

与全部既有业务库隔离；包含授权注册表、评论、分析、词频、LLM 审计五组表。
默认不保存用户昵称/头像/主页/联系方式等个人信息（导入层强制脱敏）。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.social_sentiment.config import SOCIAL_DB_PATH

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS social_sources (
    source_id VARCHAR PRIMARY KEY,
    platform VARCHAR NOT NULL,
    terms_url VARCHAR,
    checked_at VARCHAR,
    authorized_scope VARCHAR,
    permitted_operations VARCHAR,
    retention_limit_days INTEGER,
    authorization_evidence VARCHAR,
    status VARCHAR NOT NULL,
    notes VARCHAR,
    application_channel VARCHAR,
    updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS social_registry_audit (
    audit_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    old_status VARCHAR,
    new_status VARCHAR,
    evidence VARCHAR,
    operator_note VARCHAR,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS social_comments (
    comment_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    platform VARCHAR NOT NULL,
    content VARCHAR NOT NULL,
    origin_url VARCHAR,
    topic VARCHAR,
    symbol VARCHAR,
    parent_comment_id VARCHAR,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    content_hash VARCHAR NOT NULL,
    data_origin VARCHAR NOT NULL,
    authorization_ref VARCHAR,
    authorization_status VARCHAR NOT NULL,
    import_batch_id VARCHAR,
    analysis_status VARCHAR DEFAULT 'PENDING',
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS social_comment_analyses (
    analysis_id VARCHAR PRIMARY KEY,
    comment_id VARCHAR NOT NULL,
    model_id VARCHAR,
    provider VARCHAR,
    prompt_version VARCHAR,
    stance VARCHAR,
    bull_reasons VARCHAR,
    bear_reasons VARCHAR,
    asset_symbols VARCHAR,
    asset_link_confidence DOUBLE,
    topics VARCHAR,
    uncertainty VARCHAR,
    validation_flags VARCHAR,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS social_research_outputs (
    output_id VARCHAR PRIMARY KEY,
    symbol VARCHAR,
    platform VARCHAR,
    asof_at TIMESTAMPTZ NOT NULL,
    output_type VARCHAR NOT NULL,
    payload VARCHAR NOT NULL,
    data_origin_scope VARCHAR,
    model_id VARCHAR,
    created_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS social_llm_calls (
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
    con = duckdb.connect(str(SOCIAL_DB_PATH))
    con.execute("SET TimeZone='Asia/Shanghai'")
    con.execute(SCHEMA)
    from trading.wealth_lab.social_sentiment import registry as _registry
    _registry._initialize(con)
    return con


def _now() -> datetime:
    return datetime.now(_TZ)


# ------------------------------------------------------------------ 评论
def insert_comment(comment: dict) -> bool:
    """幂等写入（content_hash 去重）。返回 True=新评论。"""
    with _LOCK, connect() as con:
        exists = con.execute(
            "SELECT 1 FROM social_comments WHERE comment_id=?", [comment["comment_id"]]
        ).fetchone()
        if exists:
            return False
        con.execute(
            """INSERT INTO social_comments
               (comment_id, source_id, platform, content, origin_url, topic, symbol,
                parent_comment_id, published_at, fetched_at, content_hash, data_origin,
                authorization_ref, authorization_status, import_batch_id,
                analysis_status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [comment.get(k) for k in (
                "comment_id", "source_id", "platform", "content", "origin_url",
                "topic", "symbol", "parent_comment_id", "published_at", "fetched_at",
                "content_hash", "data_origin", "authorization_ref",
                "authorization_status", "import_batch_id", "analysis_status",
                "created_at")],
        )
        return True


def comments_for_analysis(limit: int, source_id: str | None = None,
                          include_synthetic: bool = False) -> list[dict]:
    cond = "analysis_status='PENDING'"
    args: list = []
    if source_id:
        cond += " AND source_id=?"; args.append(source_id)
    if not include_synthetic:
        cond += " AND data_origin != 'synthetic_test'"
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT comment_id, source_id, platform, content, topic, symbol,
                       published_at FROM social_comments WHERE {cond}
                ORDER BY published_at DESC NULLS LAST LIMIT ?""",
            [*args, limit],
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def set_comment_analysis_status(comment_id: str, status: str) -> None:
    with _LOCK, connect() as con:
        con.execute("UPDATE social_comments SET analysis_status=? WHERE comment_id=?",
                    [status, comment_id])


def save_analysis(record: dict) -> None:
    with _LOCK, connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO social_comment_analyses VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [record.get(k) for k in (
                "analysis_id", "comment_id", "model_id", "provider", "prompt_version",
                "stance", "bull_reasons", "bear_reasons", "asset_symbols",
                "asset_link_confidence", "topics", "uncertainty", "validation_flags",
                "created_at")],
        )


def audit_llm_call(call_id: str, agent: str, model: str | None, provider: str | None,
                   prompt: str, prompt_version: str, meta: dict) -> None:
    import hashlib
    with _LOCK, connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO social_llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [call_id, agent, model, provider, prompt_version,
             hashlib.sha256((prompt_version + prompt).encode("utf-8")).hexdigest(),
             meta.get("input_tokens"), meta.get("output_tokens"), meta.get("latency_ms"),
             meta.get("success", False), meta.get("error_type"), _now()],
        )


def save_research_output(output: dict) -> None:
    with _LOCK, connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO social_research_outputs VALUES (?,?,?,?,?,?,?,?,?)""",
            [output.get(k) for k in (
                "output_id", "symbol", "platform", "asof", "output_type", "payload",
                "data_origin_scope", "model_id", "created_at")],
        )


def latest_research_output(output_type: str, symbol: str | None = None,
                           platform: str | None = None,
                           data_origin_scope: str = "real_only") -> dict | None:
    conds = ["output_type=?", "data_origin_scope=?"]
    args: list = [output_type, data_origin_scope]
    if symbol:
        conds.append("symbol IS NOT DISTINCT FROM ?"); args.append(symbol)
    if platform:
        conds.append("platform IS NOT DISTINCT FROM ?"); args.append(platform)
    with _LOCK, connect() as con:
        row = con.execute(
            f"""SELECT output_id, symbol, platform, asof_at AS asof, output_type, payload,
                       data_origin_scope, model_id, created_at
                FROM social_research_outputs WHERE {' AND '.join(conds)}
                ORDER BY "asof" DESC LIMIT 1""", args).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in con.description]
    out = dict(zip(cols, row))
    try:
        out["payload"] = json.loads(out["payload"])
    except Exception:
        pass
    return out


def all_comments(include_synthetic: bool = False,
                 source_id: str | None = None) -> list[dict]:
    cond = "1=1"
    args: list = []
    if not include_synthetic:
        cond += " AND data_origin != 'synthetic_test'"
    if source_id:
        cond += " AND source_id=?"; args.append(source_id)
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT comment_id, source_id, platform, content, topic, symbol,
                       published_at, fetched_at, data_origin, authorization_status,
                       analysis_status FROM social_comments WHERE {cond}
                ORDER BY published_at DESC NULLS LAST""", args,
        ).fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]


def analyzed_comments(include_synthetic: bool = False) -> list[dict]:
    cond = "c.analysis_status IN ('ANALYZED','INSUFFICIENT_DATA')"
    if not include_synthetic:
        cond += " AND c.data_origin != 'synthetic_test'"
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT c.comment_id, c.source_id, c.platform, c.content, c.topic,
                       c.symbol, c.published_at, c.data_origin,
                       a.stance, a.bull_reasons, a.bear_reasons, a.asset_symbols,
                       a.asset_link_confidence, a.topics, a.uncertainty
                FROM social_comments c JOIN social_comment_analyses a
                  USING (comment_id) WHERE {cond}
                ORDER BY c.published_at DESC NULLS LAST""",
        ).fetchall()
        cols = [d[0] for d in con.description]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        for k in ("bull_reasons", "bear_reasons", "asset_symbols", "topics"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except Exception:
                d[k] = []
        out.append(d)
    return out


def coverage_stats(include_synthetic: bool = False) -> list[dict]:
    cond = "" if include_synthetic else "WHERE data_origin != 'synthetic_test'"
    with _LOCK, connect() as con:
        rows = con.execute(
            f"""SELECT source_id, platform, COUNT(*) AS comments,
                       COUNT(DISTINCT symbol) AS symbols,
                       MAX(published_at) AS latest_published_at,
                       MIN(published_at) AS earliest_published_at
                FROM social_comments {cond} GROUP BY source_id, platform""").fetchall()
        cols = [d[0] for d in con.description]
    return [dict(zip(cols, r)) for r in rows]
