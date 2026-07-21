from __future__ import annotations

import httpx


URL = (
    "https://static.cninfo.com.cn/"
    "finalpage/2026-07-15/1225422790.PDF"
)


def main() -> None:
    with httpx.Client(
        timeout=60,
        follow_redirects=True,
        trust_env=False,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.cninfo.com.cn/",
        },
    ) as client:
        response = client.get(URL)

    print(f"status_code: {response.status_code}")
    print(f"final_url: {response.url}")
    print(
        "content_type: "
        f"{response.headers.get('content-type')}"
    )
    print(f"content_length: {len(response.content)}")
    print(
        "pdf_magic: "
        f"{response.content[:5] == b'%PDF-'}"
    )


if __name__ == "__main__":
    main()