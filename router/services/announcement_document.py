from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from router.schemas import AnnouncementDocument


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_cache_dir() -> Path:
    user_data_dir = os.getenv("HERMES_OPC_USER_DATA_DIR", "").strip()
    if user_data_dir:
        return Path(user_data_dir) / "cache" / "announcements"
    return PROJECT_ROOT / "cache" / "announcements"


DEFAULT_CACHE_DIR = default_cache_dir()

ALLOWED_DETAIL_HOSTS = {
    "www.cninfo.com.cn",
    "cninfo.com.cn",
}

STATIC_HOST = "static.cninfo.com.cn"

ANNOUNCEMENT_ID_PATTERN = re.compile(
    r"^[0-9]{6,30}$"
)

MIN_PDF_BYTES = 1024
MAX_PDF_BYTES = 50 * 1024 * 1024


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def has_pdf_magic(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(5) == b"%PDF-"
    except OSError:
        return False


def parse_cninfo_detail_url(
    source_url: str,
) -> tuple[str, str]:
    parsed = urlparse(source_url)
    hostname = (parsed.hostname or "").lower()

    if hostname not in ALLOWED_DETAIL_HOSTS:
        raise ValueError(
            "公告详情 URL 不属于允许的巨潮资讯域名"
        )

    parameters = parse_qs(
        parsed.query,
        keep_blank_values=False,
    )

    announcement_ids = parameters.get(
        "announcementId"
    )
    announcement_times = parameters.get(
        "announcementTime"
    )

    if not announcement_ids:
        raise ValueError(
            "公告详情 URL 缺少 announcementId"
        )

    if not announcement_times:
        raise ValueError(
            "公告详情 URL 缺少 announcementTime"
        )

    announcement_id = announcement_ids[0].strip()
    announcement_time = (
        announcement_times[0].strip()
    )

    if not ANNOUNCEMENT_ID_PATTERN.fullmatch(
        announcement_id
    ):
        raise ValueError(
            "announcementId 格式不合法"
        )

    try:
        parsed_date = date.fromisoformat(
            announcement_time
        )
    except ValueError as exc:
        raise ValueError(
            "announcementTime 必须为 YYYY-MM-DD"
        ) from exc

    return (
        announcement_id,
        parsed_date.isoformat(),
    )


def build_static_pdf_url(
    announcement_id: str,
    announcement_time: str,
) -> str:
    if not ANNOUNCEMENT_ID_PATTERN.fullmatch(
        announcement_id
    ):
        raise ValueError(
            "announcementId 格式不合法"
        )

    normalized_date = date.fromisoformat(
        announcement_time
    ).isoformat()

    return (
        "https://static.cninfo.com.cn/"
        f"finalpage/{normalized_date}/"
        f"{announcement_id}.PDF"
    )


class AnnouncementDocumentService:
    """Fetch and cache validated CNInfo announcement PDFs."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
    ) -> None:
        self.cache_dir = cache_dir.resolve()
        self.cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _paths(
        self,
        announcement_id: str,
        announcement_time: str,
    ) -> tuple[Path, Path, Path]:
        stem = (
            f"{announcement_time}_"
            f"{announcement_id}"
        )

        pdf_path = self.cache_dir / f"{stem}.pdf"
        metadata_path = (
            self.cache_dir / f"{stem}.json"
        )
        temporary_path = (
            self.cache_dir / f"{stem}.part"
        )

        return (
            pdf_path,
            metadata_path,
            temporary_path,
        )

    def _read_cached(
        self,
        *,
        announcement_id: str,
        announcement_time: str,
        source_url: str,
        pdf_url: str,
        pdf_path: Path,
        metadata_path: Path,
    ) -> AnnouncementDocument | None:
        if not pdf_path.exists():
            return None

        size_bytes = pdf_path.stat().st_size

        if (
            size_bytes < MIN_PDF_BYTES
            or size_bytes > MAX_PDF_BYTES
            or not has_pdf_magic(pdf_path)
        ):
            pdf_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return None

        metadata: dict[str, Any] = {}

        if metadata_path.exists():
            try:
                loaded = json.loads(
                    metadata_path.read_text(
                        encoding="utf-8"
                    )
                )

                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, json.JSONDecodeError):
                metadata = {}

        digest = str(
            metadata.get("sha256")
            or sha256_file(pdf_path)
        )

        fetched_at = str(
            metadata.get("fetched_at")
            or datetime.fromtimestamp(
                pdf_path.stat().st_mtime,
                timezone.utc,
            ).isoformat()
        )

        content_type = str(
            metadata.get("content_type")
            or "application/pdf"
        )

        return AnnouncementDocument(
            announcement_id=announcement_id,
            announcement_time=announcement_time,
            source_url=source_url,
            pdf_url=pdf_url,
            local_path=str(pdf_path),
            metadata_path=str(metadata_path),
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=digest,
            cache_hit=True,
            fetched_at=fetched_at,
        )

    async def fetch(
        self,
        source_url: str,
    ) -> AnnouncementDocument:
        (
            announcement_id,
            announcement_time,
        ) = parse_cninfo_detail_url(source_url)

        pdf_url = build_static_pdf_url(
            announcement_id,
            announcement_time,
        )

        (
            pdf_path,
            metadata_path,
            temporary_path,
        ) = self._paths(
            announcement_id,
            announcement_time,
        )

        cached = self._read_cached(
            announcement_id=announcement_id,
            announcement_time=announcement_time,
            source_url=source_url,
            pdf_url=pdf_url,
            pdf_path=pdf_path,
            metadata_path=metadata_path,
        )

        if cached is not None:
            return cached

        temporary_path.unlink(missing_ok=True)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64)"
            ),
            "Referer": "https://www.cninfo.com.cn/",
            "Accept": (
                "application/pdf,"
                "application/octet-stream;q=0.9"
            ),
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=90,
                    connect=20,
                ),
                follow_redirects=True,
                trust_env=False,
                headers=headers,
            ) as client:
                async with client.stream(
                    "GET",
                    pdf_url,
                ) as response:
                    response.raise_for_status()

                    final_host = (
                        response.url.host or ""
                    ).lower()

                    if final_host != STATIC_HOST:
                        raise RuntimeError(
                            "公告 PDF 重定向到了"
                            "非预期域名"
                        )

                    content_type = (
                        response.headers.get(
                            "content-type",
                            ""
                        )
                        .split(";")[0]
                        .strip()
                        .lower()
                    )

                    allowed_types = {
                        "application/pdf",
                        "application/octet-stream",
                    }

                    if content_type not in allowed_types:
                        raise RuntimeError(
                            "公告文件 Content-Type "
                            f"异常：{content_type}"
                        )

                    declared_length = (
                        response.headers.get(
                            "content-length"
                        )
                    )

                    if declared_length:
                        expected_size = int(
                            declared_length
                        )

                        if expected_size > MAX_PDF_BYTES:
                            raise RuntimeError(
                                "公告 PDF 超过最大允许尺寸"
                            )

                    digest = hashlib.sha256()
                    size_bytes = 0

                    with temporary_path.open(
                        "wb"
                    ) as output:
                        async for chunk in (
                            response.aiter_bytes()
                        ):
                            if not chunk:
                                continue

                            size_bytes += len(chunk)

                            if size_bytes > MAX_PDF_BYTES:
                                raise RuntimeError(
                                    "公告 PDF 下载尺寸"
                                    "超过限制"
                                )

                            digest.update(chunk)
                            output.write(chunk)

        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

        if size_bytes < MIN_PDF_BYTES:
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError(
                "公告 PDF 文件尺寸异常"
            )

        if not has_pdf_magic(temporary_path):
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError(
                "公告文件未通过 PDF 文件头校验"
            )

        temporary_path.replace(pdf_path)

        fetched_at = utc_now_text()
        sha256 = digest.hexdigest()

        metadata = {
            "announcement_id": announcement_id,
            "announcement_time": announcement_time,
            "source_url": source_url,
            "pdf_url": pdf_url,
            "local_path": str(pdf_path),
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "fetched_at": fetched_at,
        }

        metadata_path.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        return AnnouncementDocument(
            announcement_id=announcement_id,
            announcement_time=announcement_time,
            source_url=source_url,
            pdf_url=pdf_url,
            local_path=str(pdf_path),
            metadata_path=str(metadata_path),
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            cache_hit=False,
            fetched_at=fetched_at,
        )
