"""事件级归并（v1.1 P0）：同一事实操作的多种媒体报道只计一个事件。

归并键（全部确定性抽取）：
  (类别, 政策主体, 操作类型, 金额集合, 发布日期 ±1 天)
- 同主体同金额同日但操作不同（MLF vs 逆回购）不得合并；
- 无金额无操作词时退化为：同主体同日 + 标题 bigram 相似度 ≥ 0.8；
- 仅对 MERGE_CATEGORIES（政策/宏观/债券/黄金操作类）启用，其余类别保持
  精确标题归并（防止过度合并）。
纯函数 + 一次库内执行入口 merge_existing_events()。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

from trading.wealth_lab.sentiment import repository as repo
from trading.wealth_lab.sentiment.config import (
    MERGE_CATEGORIES, MERGE_DAY_WINDOW, MERGE_TITLE_SIMILARITY,
    OPERATION_KEYWORDS, POLICY_ENTITIES,
)

_AMOUNT_RES: tuple[tuple[str, str], ...] = (
    (r"(\d+(?:\.\d+)?)\s*万亿", "万亿"),
    (r"(\d+(?:\.\d+)?)\s*亿", "亿"),
    (r"(\d+(?:\.\d+)?)\s*万[百千]?", "万"),
    (r"(\d+(?:\.\d+)?)\s*(?:个基点|bp|BP)", "bp"),
    (r"(\d+(?:\.\d+)?)\s*%", "%"),
)


def extract_entity(text: str) -> str | None:
    for keyword, canonical in POLICY_ENTITIES.items():
        if keyword in (text or ""):
            return canonical
    return None


def extract_operation(text: str) -> str | None:
    for op in OPERATION_KEYWORDS:
        if op.upper() in (text or "").upper():
            return op.upper()
    return None


def extract_amounts(text: str) -> list[str]:
    """金额归一（同值不同写法等价：7000亿元 / 7,000亿 → 7000亿）。"""
    found: set[str] = set()
    for pattern, unit in _AMOUNT_RES:
        for value in re.findall(pattern, text or ""):
            try:
                num = float(value.replace(",", ""))
            except ValueError:
                continue
            found.add(f"{num:g}{unit}")
    return sorted(found)


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"[\s\W]+", "", text or "")
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def title_similarity(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def same_factual_operation(event_a: dict, event_b: dict) -> bool:
    """两个事件是否描述同一事实操作（归并判定，双向对称）。"""
    if event_a.get("raw_category") != event_b.get("raw_category"):
        return False
    if event_a.get("raw_category") not in MERGE_CATEGORIES:
        return False
    pa, pb = event_a.get("published_at"), event_b.get("published_at")
    if not pa or not pb:
        return False
    if abs((pa.date() - pb.date()).days) > MERGE_DAY_WINDOW:
        return False
    ta, tb = event_a["title"], event_b["title"]
    ea, eb = extract_entity(ta), extract_entity(tb)
    if not ea or ea != eb:
        return False  # 主体必须一致且可识别
    ops_a, ops_b = extract_operation(ta), extract_operation(tb)
    am_a, am_b = extract_amounts(ta), extract_amounts(tb)
    if am_a and am_b:
        # 有金额：操作一致（或都无操作词且标题相似）+ 金额一致
        if ops_a and ops_b and ops_a != ops_b:
            return False
        if set(am_a) & set(am_b):
            return ops_a == ops_b or title_similarity(ta, tb) >= 0.6
        return False
    # 无金额：要求同操作词或标题高度相似
    if ops_a and ops_b:
        return ops_a == ops_b and title_similarity(ta, tb) >= 0.5
    return title_similarity(ta, tb) >= MERGE_TITLE_SIMILARITY


def merge_existing_events(progress=None) -> dict:
    """对库内既有事件执行归并（幂等：已 MERGED 的不再参与）。

    canonical = 最早发布者；重复事件标记 merged_into + analysis_status='MERGED'，
    条目 event_id 重指向 canonical，来源数按条目重算，分析继承（复制并留痕）。
    """
    with repo.connect() as con:
        rows = con.execute(
            """SELECT event_id, title, published_at, raw_category, news_count,
                      source_count, analysis_status, asset_links, asset_scope
               FROM wealth_news_events
               WHERE (merged_into IS NULL OR merged_into='')"""
        ).fetchall()
        cols = [d[0] for d in con.description]
    events = [dict(zip(cols, r)) for r in rows if dict(zip(cols, r))["published_at"]]
    events.sort(key=lambda e: e["published_at"])  # 最早的先做 canonical

    merged_pairs: list[dict] = []
    canonical_list = list(events)
    for candidate in events:
        if candidate.get("_consumed"):
            continue
        for other in canonical_list:
            if other is candidate or other.get("_consumed"):
                continue
            if same_factual_operation(candidate, other):
                merged_pairs.append({"canonical": candidate["event_id"],
                                     "duplicate": other["event_id"],
                                     "title": other["title"][:60]})
                other["_consumed"] = True

    if not merged_pairs:
        if progress:
            progress("事件归并：无需归并")
        return {"merged": 0, "pairs": []}

    by_id = {e["event_id"]: e for e in events}
    for pair in merged_pairs:
        canonical_id, dup_id = pair["canonical"], pair["duplicate"]
        dup = by_id[dup_id]
        canon = by_id[canonical_id]
        with repo._LOCK, repo.connect() as con:
            con.execute("UPDATE wealth_news_items SET event_id=? WHERE event_id=?",
                        [canonical_id, dup_id])
            con.execute(
                """UPDATE wealth_news_events SET merged_into=?, analysis_status='MERGED',
                   updated_at=? WHERE event_id=?""",
                [canonical_id, datetime.now(repo._TZ), dup_id],
            )
            stats = con.execute(
                """SELECT COUNT(*), COUNT(DISTINCT source_name) FROM wealth_news_items
                   WHERE event_id=?""", [canonical_id]).fetchone()
            links_json = _union_links(con, canonical_id)
            con.execute(
                """UPDATE wealth_news_events SET news_count=?, source_count=?,
                   asset_links=?, updated_at=? WHERE event_id=?""",
                [stats[0], stats[1], links_json, datetime.now(repo._TZ), canonical_id],
            )
            _inherit_analysis(con, canonical_id, dup_id)
        if progress:
            progress(f"归并 {dup_id[:16]} → {canonical_id[:16]}：{pair['title']}")
    return {"merged": len(merged_pairs),
            "pairs": [{"canonical": p["canonical"], "duplicate": p["duplicate"],
                       "duplicate_title": p["title"]} for p in merged_pairs]}


def _union_links(con, canonical_id: str) -> str:
    row = con.execute(
        "SELECT asset_links FROM wealth_news_events WHERE event_id=?",
        [canonical_id]).fetchone()
    try:
        links = json.loads(row[0]) if row and row[0] else {}
    except Exception:
        links = {}
    from trading.wealth_lab.sentiment.linking import merge_link_maps
    item_rows = con.execute(
        "SELECT asset_links FROM wealth_news_items WHERE event_id=?",
        [canonical_id]).fetchall()
    for (item_links,) in item_rows:
        try:
            links = merge_link_maps(links, json.loads(item_links or "{}"))
        except Exception:
            continue
    return json.dumps(links, ensure_ascii=False)


def _inherit_analysis(con, canonical_id: str, dup_id: str) -> None:
    """canonical 无有效分析而重复事件有 → 复制一份分析到 canonical（留痕可审计）。"""
    canon_status = con.execute(
        "SELECT analysis_status FROM wealth_news_events WHERE event_id=?",
        [canonical_id]).fetchone()[0]
    if canon_status == "ANALYZED":
        return
    dup_analysis = con.execute(
        """SELECT * FROM wealth_event_analyses WHERE event_id=? AND status='VALID'
           ORDER BY created_at DESC LIMIT 1""", [dup_id]).fetchone()
    if dup_analysis is None:
        return
    cols = [d[0] for d in con.description]
    record = dict(zip(cols, dup_analysis))
    old_id = record["analysis_id"]
    record["analysis_id"] = "wana_" + uuid.uuid4().hex[:12]
    record["event_id"] = canonical_id
    flags = record.get("validation_flags")
    try:
        flag_list = json.loads(flags or "[]")
    except Exception:
        flag_list = []
    flag_list.append(f"inherited_from_merge:{old_id}")
    record["validation_flags"] = json.dumps(flag_list, ensure_ascii=False)
    placeholders = ",".join("?" for _ in record)
    con.execute(
        f"INSERT OR REPLACE INTO wealth_event_analyses ({','.join(record.keys())}) "
        f"VALUES ({placeholders})", list(record.values()))
    con.execute(
        "UPDATE wealth_news_events SET analysis_status='ANALYZED' WHERE event_id=?",
        [canonical_id])
