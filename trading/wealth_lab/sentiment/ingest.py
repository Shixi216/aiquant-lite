"""资讯采集层：真实调用 AKShare 接口 → 标准化 → 去重 → 事件归并 → 确定性资产关联。

合规边界：只采集公开、无需登录/付费墙的授权财经资讯接口（AKShare 封装的
东财/百度公开端点）；仅存储标题/摘要/链接/时间与哈希，不批量转载全文；
发布时间不可核实的条目标记 quality_status=NO_PUBLISH_TIME，绝不伪造时间。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from trading.wealth_lab.sentiment import repository as repo
from trading.wealth_lab.sentiment.linking import extract_codes
from trading.wealth_lab.sentiment.config import (
    DEFAULT_KEYWORDS, DIRECT_HOSTS, ECO_CALENDAR_DAYS, FUND_ANNOUNCE_LOOKBACK_DAYS,
    INGEST_VERSION, KEYWORD_TOPICS, REQUEST_SLEEP_SECONDS, SOURCES, SYMBOL_CLASS,
)

_TZ = timezone(timedelta(hours=8))


def _ensure_direct_network() -> None:
    """国内财经站点直连（幂等合并 NO_PROXY，与项目网络策略一致）。"""
    existing = set(filter(None, (os_get("NO_PROXY", "") + "," + os_get("no_proxy", "")).split(",")))
    existing.update(h.lstrip(".") for h in DIRECT_HOSTS)
    merged = ",".join(sorted(existing))
    import os
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def os_get(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)


# ------------------------------------------------------------------ 工具
def _now() -> datetime:
    return datetime.now(_TZ)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _news_id(content_hash: str) -> str:
    return "wnews_" + content_hash[:20]


def _normalize_title(title: str) -> str:
    return re.sub(r"[\s\W]+", "", (title or "").lower())


def _parse_time(value) -> tuple[datetime | None, str]:
    """解析发布时间 → (tz-aware 北京时间, 精度)。失败 → (None, UNKNOWN)。"""
    if value is None:
        return None, "UNKNOWN"
    text = str(value).strip()
    if not text:
        return None, "UNKNOWN"
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                        "%Y%m%d", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None, "UNKNOWN"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    else:
        dt = dt.astimezone(_TZ)
    precision = "DAY" if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else "SECOND"
    return dt, precision


def _detect_language(text: str) -> str:
    if not text:
        return "zh"
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    return "en" if ascii_letters / max(len(text), 1) > 0.7 else "zh"


def _ingest_item(*, source_name: str, title: str, summary: str | None, url: str | None,
                 published_raw, category: str,
                 source_symbol: str | None = None,
                 keyword_theme_symbols: tuple[str, ...] = (),
                 raw_extra: dict | None = None) -> tuple[bool, str]:
    """标准化+去重+事件归并一条资讯。返回 (是否新条目, quality_status)。

    v1.1：确定性分级关联（EXACT/SAME_INDEX）+ 同一事实操作事件归并
    （政策/宏观数据类按 主体+操作+金额+日期 归并，防止重复计权）。
    """
    from trading.wealth_lab.sentiment.linking import classify_links
    published_at, precision = _parse_time(published_raw)
    linked = classify_links(title, summary, source_symbol=source_symbol,
                            keyword_theme_symbols=keyword_theme_symbols)
    symbols = sorted(linked["symbols"])
    url_hash = _sha(url) if url else ""
    content_hash = url_hash or _sha("title:" + _normalize_title(title) + "|" + (str(published_raw) or ""))
    quality = "OK" if published_at else "NO_PUBLISH_TIME"
    news_id = _news_id(content_hash)
    now = _now()
    is_new = repo.upsert_news_item({
        "news_id": news_id,
        "source_provider": "akshare",
        "source_name": source_name,
        "source_url": url,
        "title": title.strip()[:500],
        "summary": (summary or "").strip()[:2000] or None,
        "published_at": published_at,
        "published_at_precision": precision,
        "first_seen_at": now,
        "fetched_at": now,
        "content_hash": content_hash,
        "language": _detect_language(title),
        "asset_scope": json.dumps(symbols, ensure_ascii=False),
        "asset_links": json.dumps(linked["symbols"], ensure_ascii=False),
        "raw_category": category,
        "event_id": None,
        "quality_status": quality,
        "ingest_version": INGEST_VERSION,
        "raw_extra": json.dumps(raw_extra or {}, ensure_ascii=False),
    })
    if is_new:
        event_id = _resolve_event_id(title, category, published_at)
        repo.upsert_event({
            "event_id": event_id,
            "representative_news_id": news_id,
            "title": title.strip()[:500],
            "published_at": published_at,
            "published_at_precision": precision,
            "first_seen_at": now,
            "raw_category": category,
            "asset_scope": json.dumps(symbols, ensure_ascii=False),
            "asset_links": json.dumps(linked["symbols"], ensure_ascii=False),
            "news_count": 1,
            "source_count": 1,
            "analysis_status": "PENDING",
            "merged_into": None,
            "updated_at": now,
        })
        repo.link_item_to_event(news_id, event_id)
    return is_new, quality


def _resolve_event_id(title: str, category: str, published_at) -> str:
    """新事件落库前先找"同一事实操作"的既有事件（±1 天），命中则复用其 event_id。"""
    default_id = "wevt_" + _sha("event:" + _normalize_title(title))[:20]
    if published_at is None:
        return default_id
    from trading.wealth_lab.sentiment.merge import same_factual_operation
    with repo.connect() as con:
        rows = con.execute(
            """SELECT event_id, title, published_at, raw_category
               FROM wealth_news_events
               WHERE raw_category=? AND published_at BETWEEN ? AND ?
                 AND (merged_into IS NULL OR merged_into='')""",
            [category,
             published_at - timedelta(days=2), published_at + timedelta(days=2)],
        ).fetchall()
    candidate = {"title": title, "raw_category": category, "published_at": published_at}
    for event_id, other_title, other_published, other_category in rows:
        other = {"title": other_title, "raw_category": other_category,
                 "published_at": other_published}
        if same_factual_operation(candidate, other):
            return event_id
    return default_id


# ------------------------------------------------------------------ 源适配器
def _ak():
    _ensure_direct_network()
    import akshare as ak
    return ak


def _collect_keyword_news(progress=None) -> dict:
    """东财关键词资讯：每关键词一次调用，含正文摘要。"""
    ak = _ak()
    run = _new_run("eastmoney-keyword-news")
    fetched = new = dup = skipped = 0
    key_fields_total = key_fields_filled = 0
    latest = None
    try:
        for kw in DEFAULT_KEYWORDS:
            category, _klass, hint_symbols = KEYWORD_TOPICS[kw]
            try:
                frame = ak.stock_news_em(symbol=kw)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - 单关键词失败不中断源
                run.setdefault("partial_errors", []).append(f"{kw}: {type(exc).__name__}")
                time.sleep(REQUEST_SLEEP_SECONDS)
                continue
            fetched += len(frame)
            for row in frame.itertuples(index=False):
                title = str(getattr(row, "新闻标题", "") or "").strip()
                if not title:
                    skipped += 1
                    continue
                summary = str(getattr(row, "新闻内容", "") or "").strip() or None
                url = str(getattr(row, "新闻链接", "") or "").strip() or None
                published = getattr(row, "发布时间", None)
                source_label = str(getattr(row, "文章来源", "") or "").strip() or None
                key_fields_total += 4
                key_fields_filled += sum(bool(x) for x in (title, summary, url, published))
                is_new, quality = _ingest_item(
                    source_name="eastmoney-keyword-news", title=title, summary=summary,
                    url=url, published_raw=published, category=category,
                    keyword_theme_symbols=hint_symbols,
                    raw_extra={"keyword": kw, "media": source_label,
                               "about_non_pool_fund": bool(extract_codes(f"{title} {summary or ''}"))},
                )
                new += int(is_new)
                dup += int(not is_new)
                if quality != "OK":
                    pass  # NO_PUBLISH_TIME 已在库内标记
                pt, _ = _parse_time(published)
                if pt and (latest is None or pt > latest):
                    latest = pt
            time.sleep(REQUEST_SLEEP_SECONDS)
            if progress and not progress(f"关键词 {kw}: 累计新增 {new}"):
                run["status"] = "PARTIAL"
                break
        run["status"] = run.get("status") or "OK"
    except Exception as exc:  # noqa: BLE001 - 源级隔离
        run["status"] = "FAILED"
        run["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _finish_run(run, fetched, new, dup, skipped, key_fields_total, key_fields_filled, latest)
    return run


def _collect_global_flash(progress=None) -> dict:
    """东财全球财经快讯（标题+摘要+链接），QDII/海外市场主力源。"""
    ak = _ak()
    run = _new_run("eastmoney-global-flash")
    fetched = new = dup = skipped = 0
    key_fields_total = key_fields_filled = 0
    latest = None
    try:
        frame = ak.stock_info_global_em()  # type: ignore[attr-defined]
        fetched = len(frame)
        for row in frame.itertuples(index=False):
            title = str(getattr(row, "标题", "") or "").strip()
            if not title:
                skipped += 1
                continue
            summary = str(getattr(row, "摘要", "") or "").strip() or None
            url = str(getattr(row, "链接", "") or "").strip() or None
            published = getattr(row, "发布时间", None)
            key_fields_total += 4
            key_fields_filled += sum(bool(x) for x in (title, summary, url, published))
            from trading.wealth_lab.sentiment.linking import classify_links
            linked = classify_links(title, summary)
            category = "QDII_OVERSEAS" if "OVERSEAS_EQUITY" in linked["classes"] else "GENERAL"
            is_new, _q = _ingest_item(
                source_name="eastmoney-global-flash", title=title, summary=summary,
                url=url, published_raw=published, category=category,
                raw_extra={"about_non_pool_fund": linked["about_non_pool_fund"]},
            )
            new += int(is_new)
            dup += int(not is_new)
            pt, _ = _parse_time(published)
            if pt and (latest is None or pt > latest):
                latest = pt
        run["status"] = "OK"
    except Exception as exc:  # noqa: BLE001
        run["status"] = "FAILED"
        run["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _finish_run(run, fetched, new, dup, skipped, key_fields_total, key_fields_filled, latest)
    return run


def _collect_fund_announcements(progress=None, fund_symbols: list[str] | None = None) -> dict:
    """基金公告（定期报告+分红）：池内基金逐一拉取，仅保留近 60 天。"""
    ak = _ak()
    run = _new_run("eastmoney-fund-announcements")
    fetched = new = dup = skipped = 0
    key_fields_total = key_fields_filled = 0
    latest = None
    cutoff = _now() - timedelta(days=FUND_ANNOUNCE_LOOKBACK_DAYS)
    symbols = fund_symbols or sorted(SYMBOL_CLASS)
    try:
        for i, symbol in enumerate(symbols):
            code = symbol.split(".")[0]
            for api, label in ((ak.fund_announcement_report_em, "report"),  # type: ignore[attr-defined]
                               (ak.fund_announcement_dividend_em, "dividend")):  # type: ignore[attr-defined]
                try:
                    frame = api(symbol=code)
                except Exception:  # noqa: BLE001 - akshare 对"无分红公告"空结果会抛内部错误
                    # 该基金该类公告为空/接口异常：计为跳过，继续下一只（不伪造数据）
                    skipped += 1
                    run.setdefault("partial_errors", []).append(f"{code}/{label}: empty-or-error")
                    time.sleep(REQUEST_SLEEP_SECONDS)
                    continue
                fetched += len(frame)
                for row in frame.itertuples(index=False):
                    title = str(getattr(row, "公告标题", "") or "").strip()
                    announce_date = getattr(row, "公告日期", None)
                    report_id = str(getattr(row, "报告ID", "") or "").strip() or None
                    fund_name = str(getattr(row, "基金名称", "") or "").strip() or None
                    if not title:
                        skipped += 1
                        continue
                    pt, _prec = _parse_time(announce_date)
                    if pt and pt < cutoff:
                        skipped += 1
                        continue  # 超窗历史公告不入库（当前研究模式）
                    key_fields_total += 3
                    key_fields_filled += sum(bool(x) for x in (title, announce_date, report_id))
                    is_new, _q = _ingest_item(
                        source_name="eastmoney-fund-announcements", title=title,
                        summary=f"{fund_name or ''} 公告（{label}）".strip(),
                        url=f"emfund://{report_id}" if report_id else None,
                        published_raw=announce_date, category="FUND_ANNOUNCE",
                        source_symbol=symbol,
                        raw_extra={"fund_symbol": symbol, "fund_name": fund_name,
                                   "announce_kind": label, "report_id": report_id},
                    )
                    new += int(is_new)
                    dup += int(not is_new)
                    if pt and (latest is None or pt > latest):
                        latest = pt
                time.sleep(REQUEST_SLEEP_SECONDS)
            if progress and not progress(f"{symbol}: 累计新增 {new}"):
                run["status"] = "PARTIAL"
                break
        run["status"] = run.get("status") or "OK"
    except Exception as exc:  # noqa: BLE001
        run["status"] = "FAILED"
        run["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _finish_run(run, fetched, new, dup, skipped, key_fields_total, key_fields_filled, latest)
    return run


def _collect_eco_calendar(progress=None) -> dict:
    """百度经济日历：今天+昨天，仅保留已公布且地区为中国的条目。"""
    ak = _ak()
    run = _new_run("baidu-eco-calendar")
    fetched = new = dup = skipped = 0
    key_fields_total = key_fields_filled = 0
    latest = None
    try:
        for offset in range(ECO_CALENDAR_DAYS):
            day = (date.today() - timedelta(days=offset)).strftime("%Y%m%d")
            frame = ak.news_economic_baidu(date=day)  # type: ignore[attr-defined]
            fetched += len(frame)
            for row in frame.itertuples(index=False):
                region = str(getattr(row, "地区", "") or "")
                event_name = str(getattr(row, "事件", "") or "").strip()
                actual = str(getattr(row, "公布", "") or "").strip()
                if "中国" not in region or not event_name or not actual:
                    skipped += 1
                    continue  # 未公布/非中国不入事件流
                d = str(getattr(row, "日期", "") or "")
                t = str(getattr(row, "时间", "") or "").strip()
                published_raw = f"{d} {t}".strip() if t else (d or None)
                expected = str(getattr(row, "预期", "") or "").strip() or None
                previous = str(getattr(row, "前值", "") or "").strip() or None
                importance = int(getattr(row, "重要性", 0) or 0)
                key_fields_total += 4
                key_fields_filled += sum(bool(x) for x in (event_name, actual, d, region))
                title = f"{event_name} 公布值{actual}" + (f" 预期{expected}" if expected else "")
                category = "MACRO_DATA"
                is_new, _q = _ingest_item(
                    source_name="baidu-eco-calendar", title=title,
                    summary=(f"前值{previous}。" if previous else None),
                    url=f"baidu-eco://{day}/{_sha(event_name)[:10]}",
                    published_raw=published_raw, category=category,
                    raw_extra={"region": region, "event": event_name, "actual": actual,
                               "expected": expected, "previous": previous,
                               "importance": importance},
                )
                new += int(is_new)
                dup += int(not is_new)
                pt, _prec = _parse_time(published_raw)
                if pt and (latest is None or pt > latest):
                    latest = pt
            time.sleep(REQUEST_SLEEP_SECONDS)
        run["status"] = "OK"
    except Exception as exc:  # noqa: BLE001
        run["status"] = "FAILED"
        run["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _finish_run(run, fetched, new, dup, skipped, key_fields_total, key_fields_filled, latest)
    return run


ADAPTERS = {
    "keyword_news": _collect_keyword_news,
    "global_flash": _collect_global_flash,
    "fund_announcements": _collect_fund_announcements,
    "eco_calendar": _collect_eco_calendar,
}


# ------------------------------------------------------------------ run 记录
def _new_run(source_name: str) -> dict:
    return {"run_id": "wsrun_" + uuid.uuid4().hex[:12], "source_name": source_name,
            "started_at": _now(), "status": "OK", "items_fetched": 0, "items_new": 0,
            "items_duplicate": 0, "items_skipped": 0, "field_completeness": None,
            "latest_published_at": None, "error": None, "ingest_version": INGEST_VERSION}


def _finish_run(run: dict, fetched: int, new: int, dup: int, skipped: int,
                key_total: int, key_filled: int, latest: datetime | None) -> None:
    run.update({
        "finished_at": _now(), "items_fetched": fetched, "items_new": new,
        "items_duplicate": dup, "items_skipped": skipped,
        "field_completeness": round(key_filled / key_total, 4) if key_total else None,
        "latest_published_at": latest,
    })
    partial = run.pop("partial_errors", [])
    if partial and run["status"] == "OK":
        run["error"] = f"partial ({len(partial)}): " + "; ".join(partial[:6])
    repo.save_source_run(run)


def refresh(sources: list[str] | None = None, fund_symbols: list[str] | None = None,
            progress=None) -> list[dict]:
    """执行一次真实采集。sources 传 None 时采集全部启用源。"""
    runs = []
    for name, meta in SOURCES.items():
        if sources is not None and name not in sources:
            continue
        if not meta["enabled"] and sources is None:
            continue
        adapter = ADAPTERS.get(meta["adapter"])
        if adapter is None:
            continue
        if meta["adapter"] == "fund_announcements":
            run = adapter(progress, fund_symbols)
        else:
            run = adapter(progress)
        runs.append(run)
    return runs
