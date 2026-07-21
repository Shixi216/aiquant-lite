from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from router.schemas import (
    AnnouncementDocument,
    AnnouncementTextDocument,
)
from router.services.announcement_document import (
    DEFAULT_CACHE_DIR,
)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def normalize_page_text(value: str) -> str:
    text = (
        value.replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u3000", " ")
    )

    lines: list[str] = []

    for line in text.splitlines():
        normalized = re.sub(
            r"[ \t]+",
            " ",
            line,
        ).strip()

        lines.append(normalized)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def detect_ocr_requirement(
    page_char_counts: list[int],
) -> bool:
    if not page_char_counts:
        return True

    total_chars = sum(page_char_counts)
    page_count = len(page_char_counts)

    minimum_total = max(
        120,
        page_count * 80,
    )

    readable_pages = sum(
        count >= 30
        for count in page_char_counts
    )

    minimum_readable_pages = max(
        1,
        (page_count + 1) // 2,
    )

    return (
        total_chars < minimum_total
        or readable_pages < minimum_readable_pages
    )


class AnnouncementTextService:
    """Extract and cache the native text layer of a PDF."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
    ) -> None:
        self.cache_dir = cache_dir.resolve()
        self.cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _validate_pdf_path(
        self,
        document: AnnouncementDocument,
    ) -> Path:
        pdf_path = Path(document.local_path).resolve()

        try:
            pdf_path.relative_to(self.cache_dir)
        except ValueError as exc:
            raise ValueError(
                "公告 PDF 不在允许的缓存目录内"
            ) from exc

        if not pdf_path.exists():
            raise FileNotFoundError(
                f"公告 PDF 不存在：{pdf_path}"
            )

        actual_sha256 = sha256_file(pdf_path)

        if actual_sha256 != document.sha256:
            raise RuntimeError(
                "公告 PDF 的 SHA-256 与下载记录不一致"
            )

        return pdf_path

    def _text_paths(
        self,
        pdf_path: Path,
    ) -> tuple[Path, Path, Path, Path]:
        text_path = pdf_path.with_suffix(".txt")
        metadata_path = pdf_path.with_suffix(
            ".text.json"
        )
        text_temporary_path = pdf_path.with_suffix(
            ".txt.part"
        )
        metadata_temporary_path = pdf_path.with_suffix(
            ".text.json.part"
        )

        return (
            text_path,
            metadata_path,
            text_temporary_path,
            metadata_temporary_path,
        )

    def _read_cached(
        self,
        *,
        document: AnnouncementDocument,
        pdf_path: Path,
        text_path: Path,
        metadata_path: Path,
    ) -> AnnouncementTextDocument | None:
        if (
            not text_path.exists()
            or not metadata_path.exists()
        ):
            return None

        try:
            metadata_raw = json.loads(
                metadata_path.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(metadata_raw, dict):
            return None

        if (
            metadata_raw.get("source_pdf_sha256")
            != document.sha256
        ):
            return None

        try:
            text = text_path.read_text(
                encoding="utf-8"
            )
        except OSError:
            return None

        text_sha256 = sha256_bytes(
            text.encode("utf-8")
        )

        if (
            metadata_raw.get("text_sha256")
            != text_sha256
        ):
            return None

        page_char_counts = metadata_raw.get(
            "page_char_counts"
        )

        if not isinstance(page_char_counts, list):
            return None

        if not all(
            isinstance(value, int) and value >= 0
            for value in page_char_counts
        ):
            return None

        try:
            page_count = int(
                metadata_raw["page_count"]
            )
            requires_ocr = bool(
                metadata_raw["requires_ocr"]
            )
            extracted_at = str(
                metadata_raw["extracted_at"]
            )
        except (KeyError, TypeError, ValueError):
            return None

        return AnnouncementTextDocument(
            announcement_id=document.announcement_id,
            announcement_time=(
                document.announcement_time
            ),
            source_pdf_sha256=document.sha256,
            text_sha256=text_sha256,
            pdf_path=str(pdf_path),
            text_path=str(text_path),
            metadata_path=str(metadata_path),
            page_count=page_count,
            page_char_counts=page_char_counts,
            text_length=len(text),
            extract_method="pymupdf_text",
            requires_ocr=requires_ocr,
            cache_hit=True,
            extracted_at=extracted_at,
            text=text,
        )

    def extract(
        self,
        document: AnnouncementDocument,
    ) -> AnnouncementTextDocument:
        pdf_path = self._validate_pdf_path(document)

        (
            text_path,
            metadata_path,
            text_temporary_path,
            metadata_temporary_path,
        ) = self._text_paths(pdf_path)

        cached = self._read_cached(
            document=document,
            pdf_path=pdf_path,
            text_path=text_path,
            metadata_path=metadata_path,
        )

        if cached is not None:
            return cached

        page_texts: list[str] = []
        page_char_counts: list[int] = []

        try:
            with pymupdf.open(pdf_path) as pdf:
                if pdf.needs_pass:
                    raise RuntimeError(
                        "公告 PDF 已加密，无法直接提取"
                    )

                if pdf.page_count <= 0:
                    raise RuntimeError(
                        "公告 PDF 没有有效页面"
                    )

                for page_index in range(
                    pdf.page_count
                ):
                    page = pdf.load_page(page_index)

                    raw_text = page.get_text(
                        "text",
                        sort=True,
                    )

                    normalized = normalize_page_text(
                        raw_text
                    )

                    page_texts.append(normalized)
                    page_char_counts.append(
                        len(normalized)
                    )

                page_count = pdf.page_count

        except Exception as exc:
            raise RuntimeError(
                "公告 PDF 文本提取失败："
                f"{type(exc).__name__}: {exc}"
            ) from exc

        sections = [
            (
                f"--- PAGE {index} ---\n"
                f"{page_text}"
            )
            for index, page_text in enumerate(
                page_texts,
                start=1,
            )
        ]

        combined_text = "\n\n".join(sections)
        requires_ocr = detect_ocr_requirement(
            page_char_counts
        )

        text_sha256 = sha256_bytes(
            combined_text.encode("utf-8")
        )
        extracted_at = utc_now_text()

        metadata: dict[str, Any] = {
            "announcement_id": (
                document.announcement_id
            ),
            "announcement_time": (
                document.announcement_time
            ),
            "source_pdf_sha256": document.sha256,
            "text_sha256": text_sha256,
            "pdf_path": str(pdf_path),
            "text_path": str(text_path),
            "page_count": page_count,
            "page_char_counts": page_char_counts,
            "text_length": len(combined_text),
            "extract_method": "pymupdf_text",
            "requires_ocr": requires_ocr,
            "extracted_at": extracted_at,
        }

        text_temporary_path.unlink(
            missing_ok=True
        )
        metadata_temporary_path.unlink(
            missing_ok=True
        )

        try:
            text_temporary_path.write_text(
                combined_text,
                encoding="utf-8",
            )

            metadata_temporary_path.write_text(
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            text_temporary_path.replace(text_path)
            metadata_temporary_path.replace(
                metadata_path
            )

        except Exception:
            text_temporary_path.unlink(
                missing_ok=True
            )
            metadata_temporary_path.unlink(
                missing_ok=True
            )
            raise

        return AnnouncementTextDocument(
            announcement_id=document.announcement_id,
            announcement_time=(
                document.announcement_time
            ),
            source_pdf_sha256=document.sha256,
            text_sha256=text_sha256,
            pdf_path=str(pdf_path),
            text_path=str(text_path),
            metadata_path=str(metadata_path),
            page_count=page_count,
            page_char_counts=page_char_counts,
            text_length=len(combined_text),
            extract_method="pymupdf_text",
            requires_ocr=requires_ocr,
            cache_hit=False,
            extracted_at=extracted_at,
            text=combined_text,
        )