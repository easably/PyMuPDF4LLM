"""Read one PDF with PyMuPDF4LLM and return markdown for each non-empty page.

Text pages are copied from the text layer. A page with no selectable text is
recognized by the OCR engine installed in this image. ``force_ocr`` is never
turned on: that would re-read every text page.
"""

from __future__ import annotations

import re
from typing import Any

import pymupdf
import pymupdf4llm

_OCR_LANGUAGE = re.compile(r"^[A-Za-z]{3}(\+[A-Za-z]{3})*$")


class InvalidPdf(ValueError):
    """The bytes are not a PDF this service can open."""


class UnsupportedOcrLanguage(ValueError):
    """``ocr_language`` is not a short engine code such as ``eng`` or ``eng+rus``."""


def pdf_to_page_markdown(pdf_bytes: bytes, *, ocr_language: str = "eng") -> dict[str, Any]:
    """Return ``page_count`` and ``pages`` of ``{page, markdown}``.

    Empty pages are omitted. ``page`` is 1-based.
    """
    language = _ocr_language_code(ocr_language)
    if not pdf_bytes.startswith(b"%PDF"):
        raise InvalidPdf("file is not a PDF")

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise InvalidPdf("file is not a readable PDF") from exc
    try:
        page_count = document.page_count
        chunks = pymupdf4llm.to_markdown(
            document,
            page_chunks=True,
            use_ocr=True,
            force_ocr=False,
            ocr_language=language,
            show_progress=False,
        )
    finally:
        document.close()

    if not isinstance(chunks, list):
        raise RuntimeError("PyMuPDF4LLM did not return one chunk per page")

    return {"page_count": page_count, "pages": _non_empty_pages(chunks)}


def _non_empty_pages(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for chunk in chunks:
        markdown = str(chunk.get("text") or "").strip()
        page = _page_number(chunk)
        if not markdown or page is None:
            continue
        pages.append({"page": page, "markdown": markdown})
    return pages


def _ocr_language_code(ocr_language: str) -> str:
    language = (ocr_language or "eng").strip()
    if not _OCR_LANGUAGE.fullmatch(language):
        raise UnsupportedOcrLanguage("ocr_language must look like eng or eng+rus")
    return language


def _page_number(chunk: dict[str, Any]) -> int | None:
    metadata = chunk.get("metadata") or {}
    for key in ("page_number", "page"):
        value = metadata.get(key)
        if isinstance(value, int) and value >= 1:
            return value
    return None
