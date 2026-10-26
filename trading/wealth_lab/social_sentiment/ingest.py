"""平台无关的评论导入层（只接受合法授权数据与获准导入文件）。

- 闸门：source 必须在注册表中为 AUTHORIZED 且 permitted_operations 含
  import_comments + store_content；否则拒绝（含 PROHIBITED 原因）；
- 脱敏：载荷出现昵称/头像/主页/联系方式等个人信息字段直接整批拒绝
  （不静默丢弃——导入方必须从源头不携带 PII）；
- 去重：content_hash = sha256(source_id + 归一化正文 + 发布时间)；
- 保留期：fetched_at 入库，registry.enforce_retention() 按源清理；
- 采集：collect_from_platform 是闸门桩——当前没有任何源拿到 auto_collect
  授权，一律拒绝；拿到授权后才允许接入真实采集器实现。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from trading.wealth_lab.social_sentiment import registry
from trading.wealth_lab.social_sentiment.config import PII_FIELDS_REJECTED
from trading.wealth_lab.social_sentiment.repository import _now, insert_comment

_TZ = timezone(timedelta(hours=8))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def collect_from_platform(source_id: str, **_kwargs) -> dict:
    """自动采集闸门桩：注册表无 auto_collect 授权时一律拒绝（防误上线）。"""
    try:
        registry.ensure_operation_allowed(source_id, "auto_collect")
    except PermissionError as exc:
        return {"ok": False, "refused": True, "source_id": source_id,
                "reason": str(exc),
                "note": "未授权平台的自动采集保持禁用；申请渠道见 GET /sources"}
    # 当前不存在已授权 auto_collect 的源；即使未来置为 AUTHORIZED，
    # 也必须在此处实现并审查具体采集器后才允许返回真实结果。
    return {"ok": False, "refused": True, "source_id": source_id,
            "reason": "采集器实现尚未接入（授权已具备，但无已审查的采集器代码）"}


def import_comments(source_id: str, comments: list[dict],
                    authorization_assertion: str | None = None,
                    data_origin: str = "real") -> dict:
    """导入一批评论。返回 {imported, duplicate, rejected, errors[]}。

    每条评论字段：content(必填)、origin_url、topic、symbol(池内代码)、
    parent_comment_id、published_at。禁止携带 PII 字段（整批拒绝）。
    """
    source = registry.ensure_operation_allowed(source_id, "import_comments")
    registry.ensure_operation_allowed(source_id, "store_content")
    if source_id == "manual-authorized-file" and not (authorization_assertion or "").strip():
        raise ValueError("manual-authorized-file 来源导入必须提交 authorization_assertion"
                         "（授权依据声明，如书面许可/协议编号/授权链接）")
    if data_origin == "synthetic_test" and source_id != "synthetic-test-lab":
        raise ValueError("synthetic_test 数据只允许来自 synthetic-test-lab 来源，"
                         "严禁以合成数据冒充真实研究")

    from trading.wealth_lab.sentiment.config import SYMBOL_CLASS
    batch_id = "wsimp_" + uuid.uuid4().hex[:12]
    imported = duplicate = 0
    errors: list[str] = []
    now = _now()
    for idx, raw in enumerate(comments):
        try:
            pii = sorted(set(PII_FIELDS_REJECTED) & set(raw.keys()))
            if pii:
                raise ValueError(f"第 {idx} 条含个人信息字段 {pii}（禁止导入 PII）")
            content = str(raw.get("content") or "").strip()
            if not content or len(content) < 4:
                raise ValueError(f"第 {idx} 条 content 缺失或过短")
            symbol = raw.get("symbol")
            if symbol and symbol not in SYMBOL_CLASS:
                raise ValueError(f"第 {idx} 条 symbol 不在研究池: {symbol}")
            published = _parse_time(raw.get("published_at"))
            content_hash = hashlib.sha256(
                f"{source_id}|{_normalize(content)}|{published or ''}".encode("utf-8")
            ).hexdigest()
            is_new = insert_comment({
                "comment_id": "wscom_" + content_hash[:20],
                "source_id": source_id,
                "platform": source["platform"],
                "content": content[:2000],
                "origin_url": (str(raw["origin_url"]) if raw.get("origin_url") else None),
                "topic": (str(raw["topic"])[:100] if raw.get("topic") else None),
                "symbol": symbol,
                "parent_comment_id": raw.get("parent_comment_id"),
                "published_at": published,
                "fetched_at": now,
                "content_hash": content_hash,
                "data_origin": data_origin,
                "authorization_ref": authorization_assertion
                or source["authorization_evidence"],
                "authorization_status": source["status"],
                "import_batch_id": batch_id,
                "analysis_status": "PENDING",
                "created_at": now,
            })
            imported += int(is_new)
            duplicate += int(not is_new)
        except Exception as exc:  # noqa: BLE001 - 逐条拒绝，不影响整批其余条目
            errors.append(str(exc)[:200])
    return {"import_batch_id": batch_id, "source_id": source_id,
            "imported": imported, "duplicate": duplicate,
            "rejected": len(errors), "errors": errors[:20]}
