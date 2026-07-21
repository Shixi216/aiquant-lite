from __future__ import annotations

import json
import re
from datetime import date
from typing import Any
from urllib.parse import urljoin

import httpx
from lxml import html


DATA_HUB_URL = "http://127.0.0.1:8766"
SYMBOL = "600172.SH"

DOCUMENT_PATTERNS = (
    ".pdf",
    "static.cninfo.com.cn",
    "announcementid",
    "finalpage",
)


def shorten(value: str, limit: int = 300) -> str:
    normalized = " ".join(value.split())

    if len(normalized) > limit:
        return normalized[:limit] + "...<truncated>"

    return normalized


def latest_announcement() -> dict[str, Any]:
    today = date.today()
    start_date = date(
        today.year,
        1,
        1,
    ).isoformat()

    with httpx.Client(
        base_url=DATA_HUB_URL,
        timeout=180,
        trust_env=False,
    ) as client:
        response = client.get(
            f"/v1/stocks/{SYMBOL}/announcements",
            params={
                "start_date": start_date,
                "end_date": today.isoformat(),
            },
        )

    response.raise_for_status()
    payload = response.json()
    records = payload.get("records")

    if not isinstance(records, list) or not records:
        raise RuntimeError("没有找到公告记录")

    record = records[0]

    if not isinstance(record, dict):
        raise RuntimeError("最新公告记录不是对象")

    return record


def extract_candidate_urls(
    content: str,
    base_url: str,
) -> list[str]:
    candidates: set[str] = set()

    try:
        document = html.fromstring(content)

        for value in document.xpath(
            "//a/@href | //iframe/@src | //embed/@src | "
            "//object/@data | //script/@src"
        ):
            if not isinstance(value, str):
                continue

            absolute_url = urljoin(base_url, value)

            if any(
                pattern in absolute_url.lower()
                for pattern in DOCUMENT_PATTERNS
            ):
                candidates.add(absolute_url)

    except Exception:
        pass

    url_pattern = re.compile(
        r"""https?://[^\s"'<>\\]+""",
        re.IGNORECASE,
    )

    for match in url_pattern.findall(content):
        cleaned = match.rstrip("),;]}")

        if any(
            pattern in cleaned.lower()
            for pattern in DOCUMENT_PATTERNS
        ):
            candidates.add(cleaned)

    return sorted(candidates)


def main() -> None:
    record = latest_announcement()
    data = record.get("data")

    if not isinstance(data, dict):
        data = {}

    announcement_url = str(
        data.get("url")
        or record.get("source_url")
        or ""
    ).strip()

    if not announcement_url:
        raise RuntimeError("公告记录没有 URL")

    print("Latest announcement:")
    print(f"record_id={record.get('record_id')}")
    print(f"title={data.get('title')}")
    print(f"event_time={record.get('event_time')}")
    print(f"source_level={record.get('source_level')}")
    print(f"verified={record.get('verified')}")
    print(f"url={announcement_url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/126 Safari/537.36"
        ),
        "Referer": "http://www.cninfo.com.cn/",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/pdf;q=0.9,*/*;q=0.8"
        ),
    }

    with httpx.Client(
        timeout=httpx.Timeout(
            timeout=60,
            connect=20,
        ),
        follow_redirects=True,
        trust_env=False,
        headers=headers,
    ) as client:
        response = client.get(announcement_url)

    content_type = response.headers.get(
        "content-type",
        "",
    )
    content_length = len(response.content)

    print("\nDocument response:")
    print(f"status_code={response.status_code}")
    print(f"final_url={response.url}")
    print(f"content_type={content_type}")
    print(f"content_length={content_length}")
    print(f"redirect_count={len(response.history)}")

    if response.status_code != 200:
        print(
            "response_sample="
            f"{shorten(response.text, 800)}"
        )
        raise RuntimeError(
            "公告页面访问失败："
            f"HTTP {response.status_code}"
        )

    if "application/pdf" in content_type.lower():
        print("document_type=pdf")
        print("direct_pdf_access=True")
        print(
            "\nAnnouncement document inspection passed"
        )
        return

    print("document_type=html_or_other")
    print(
        "response_sample="
        f"{shorten(response.text, 800)}"
    )

    candidates = extract_candidate_urls(
        response.text,
        str(response.url),
    )

    print("\nCandidate document URLs:")
    print(f"candidate_count={len(candidates)}")

    for index, candidate in enumerate(
        candidates[:20]
    ):
        print(f"{index}: {candidate}")

    script_texts: list[str] = []

    try:
        document = html.fromstring(response.text)

        for script in document.xpath("//script/text()"):
            if not isinstance(script, str):
                continue

            lowered = script.lower()

            if any(
                pattern in lowered
                for pattern in DOCUMENT_PATTERNS
            ):
                script_texts.append(
                    shorten(script, 1000)
                )

    except Exception:
        pass

    print("\nRelevant script samples:")
    print(f"script_sample_count={len(script_texts)}")

    for index, script_text in enumerate(
        script_texts[:5]
    ):
        print(f"{index}: {script_text}")

    result = {
        "record_id": record.get("record_id"),
        "title": data.get("title"),
        "source_url": announcement_url,
        "status_code": response.status_code,
        "final_url": str(response.url),
        "content_type": content_type,
        "content_length": content_length,
        "candidate_urls": candidates[:20],
    }

    print("\nInspection summary:")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "\nAnnouncement document inspection passed"
    )


if __name__ == "__main__":
    main()