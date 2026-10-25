"""Source Authorization Registry：社区数据源授权注册表（唯一采集/导入闸门）。

规则：
- 注册表落库（social_sources），首次初始化写入种子；之后种子只作参照，
  状态变更必须走 set_status（要求 authorization_evidence，全程审计）；
- ensure_operation_allowed(source_id, operation)：唯一放行入口；
  非 AUTHORIZED 或 permitted_operations 不含该操作 → PermissionError（带原因）；
- robots.txt 不作为授权证据。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from trading.wealth_lab.social_sentiment.config import REGISTRY_SEED, SOCIAL_DB_PATH

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.RLock()  # connect()→_initialize 重入安全




def connect():
    from trading.wealth_lab.social_sentiment import repository as repo
    return repo.connect()  # 同库统一建表（含注册表），并完成种子初始化


def _initialize(con) -> None:
    """首次建库写入种子（幂等：已有行不覆盖，人工状态不被种子回滚）。"""
    with _LOCK:
        existing = {r[0] for r in con.execute(
            "SELECT source_id FROM social_sources").fetchall()}
        now = datetime.now(_TZ)
        for seed in REGISTRY_SEED:
            if seed["source_id"] in existing:
                continue
            con.execute(
                """INSERT INTO social_sources
                   (source_id, platform, terms_url, checked_at, authorized_scope,
                    permitted_operations, retention_limit_days,
                    authorization_evidence, status, notes, application_channel,
                    updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [seed["source_id"], seed["platform"], seed["terms_url"],
                 seed["checked_at"], seed["authorized_scope"],
                 json.dumps(seed["permitted_operations"], ensure_ascii=False),
                 seed["retention_limit_days"], seed["authorization_evidence"],
                 seed["status"], seed["notes"], seed["application_channel"], now],
            )
            _audit_row(con, seed["source_id"], "seed_init", None, seed["status"],
                       seed["authorization_evidence"], "注册表种子初始化")


def _audit_row(con, source_id: str, action: str, old: str | None, new: str | None,
               evidence: str | None, note: str | None) -> None:
    import uuid
    con.execute(
        "INSERT INTO social_registry_audit VALUES (?,?,?,?,?,?,?,?)",
        ["wsreg_" + uuid.uuid4().hex[:12], source_id, action, old, new,
         evidence, note, datetime.now(_TZ)],
    )


def get_source(source_id: str) -> dict | None:
    with _LOCK, connect() as con:
        row = con.execute(
            "SELECT source_id, platform, terms_url, checked_at, authorized_scope,"
            " permitted_operations, retention_limit_days, authorization_evidence,"
            " status, notes, application_channel, updated_at"
            " FROM social_sources WHERE source_id=?", [source_id]).fetchone()
        if row is None:
            return None
        cols = ["source_id", "platform", "terms_url", "checked_at", "authorized_scope",
                "permitted_operations", "retention_limit_days",
                "authorization_evidence", "status", "notes", "application_channel",
                "updated_at"]
    out = dict(zip(cols, row))
    try:
        out["permitted_operations"] = json.loads(out["permitted_operations"] or "[]")
    except Exception:
        out["permitted_operations"] = []
    return out


def list_sources() -> list[dict]:
    with _LOCK, connect() as con:
        rows = con.execute(
            "SELECT source_id, platform, terms_url, checked_at, authorized_scope,"
            " permitted_operations, retention_limit_days, authorization_evidence,"
            " status, notes, application_channel, updated_at"
            " FROM social_sources ORDER BY source_id").fetchall()
        cols = ["source_id", "platform", "terms_url", "checked_at", "authorized_scope",
                "permitted_operations", "retention_limit_days",
                "authorization_evidence", "status", "notes", "application_channel",
                "updated_at"]
    out = []
    for row in rows:
        item = dict(zip(cols, row))
        try:
            item["permitted_operations"] = json.loads(item["permitted_operations"] or "[]")
        except Exception:
            item["permitted_operations"] = []
        item["collector_enabled"] = (
            item["status"] == "AUTHORIZED" and "auto_collect" in item["permitted_operations"])
        if not item["collector_enabled"]:
            item["disabled_reason"] = _disabled_reason(item)
        out.append(item)
    return out


def _disabled_reason(source: dict) -> str:
    status = source["status"]
    if status == "PROHIBITED":
        return "平台协议明文禁止（未授权采集不得上线）：" + (source["notes"] or "")
    if status == "PENDING_AUTHORIZATION":
        return "授权申请通道存在但尚未获得授权；申请渠道：" + (
            source["application_channel"] or "见 terms_url")
    if status == "UNVERIFIED":
        return "数据使用权限未完成核查，默认关闭：" + (source["notes"] or "")
    return "AUTHORIZED 但 permitted_operations 不含 auto_collect"


def ensure_operation_allowed(source_id: str, operation: str) -> dict:
    """唯一放行入口：未授权即抛 PermissionError（含具体原因）。"""
    source = get_source(source_id)
    if source is None:
        raise PermissionError(f"未知数据源: {source_id}（不在注册表，默认关闭）")
    if source["status"] != "AUTHORIZED":
        raise PermissionError(
            f"来源 {source_id}（{source['platform']}）状态为 {source['status']}，"
            f"禁止 {operation}。原因：{_disabled_reason(source)}")
    if operation not in source["permitted_operations"]:
        raise PermissionError(
            f"来源 {source_id} 授权范围不含操作 {operation}"
            f"（permitted_operations={source['permitted_operations']}）")
    return source


def set_status(source_id: str, new_status: str, evidence: str,
               permitted_operations: list[str] | None = None,
               authorized_scope: str | None = None,
               retention_limit_days: int | None = None,
               operator_note: str | None = None) -> dict:
    """状态变更（人工授权管理）：必须提交授权证据，否则拒绝。"""
    from trading.wealth_lab.social_sentiment.config import STATUSES
    if new_status not in STATUSES:
        raise ValueError(f"非法状态: {new_status}（合法值 {STATUSES}）")
    if new_status == "AUTHORIZED" and not (evidence and evidence.strip()):
        raise ValueError("置为 AUTHORIZED 必须提供 authorization_evidence（授权依据）")
    source = get_source(source_id)
    if source is None:
        raise KeyError(f"未知数据源: {source_id}")
    with _LOCK, connect() as con:
        con.execute(
            """UPDATE social_sources SET status=?, permitted_operations=?,
               authorized_scope=coalesce(?, authorized_scope),
               retention_limit_days=coalesce(?, retention_limit_days),
               authorization_evidence=?, checked_at=?, updated_at=?
               WHERE source_id=?""",
            [new_status,
             json.dumps(permitted_operations if permitted_operations is not None
                        else source["permitted_operations"], ensure_ascii=False),
             authorized_scope, retention_limit_days, evidence,
             datetime.now(_TZ).strftime("%Y-%m-%d"), datetime.now(_TZ), source_id],
        )
        _audit_row(con, source_id, "set_status", source["status"], new_status,
                   evidence, operator_note)
    return get_source(source_id)


def enforce_retention() -> int:
    """按各源 retention_limit_days 清理过期评论（合规保留期控制）。返回删除行数。"""
    with _LOCK, connect() as con:
        rows = con.execute(
            "SELECT source_id, retention_limit_days FROM social_sources").fetchall()
        now = datetime.now(_TZ)
        deleted = 0
        for source_id, days in rows:
            if not days or days <= 0:
                continue
            cutoff = now - timedelta(days=days)
            deleted += con.execute(
                "DELETE FROM social_comments WHERE source_id=? AND fetched_at < ?",
                [source_id, cutoff],
            ).fetchone()[0]
    return deleted
