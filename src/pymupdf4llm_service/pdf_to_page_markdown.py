"""Read one PDF with PyMuPDF4LLM and return markdown for each non-empty page.

Text pages are copied from the text layer. A page with no selectable text is
recognized by the Tesseract engine built into PyMuPDF. Language files come
from ``TESSDATA_PREFIX``. ``force_ocr`` is never turned on: that would re-read
every text page.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pymupdf
import pymupdf4llm

from pymupdf4llm_service.pictures import cut_embedded_pictures

# Tesseract codes, including chi_sim and script/Latin. Several may be joined with +.
_LANGUAGE_PART = re.compile(r"(?:[A-Za-z][A-Za-z0-9_]*|script/[A-Za-z][A-Za-z0-9_]*)")


class InvalidPdf(ValueError):
    """The bytes are not a PDF this service can open."""


class UnsupportedOcrLanguage(ValueError):
    """``ocr_language`` is not an installed Tesseract language code."""


class OcrUnavailable(RuntimeError):
    """Tesseract is built in, but its language files are not installed."""


def pdf_to_page_markdown(pdf_bytes: bytes, *, ocr_language: str = "eng") -> dict[str, Any]:
    """Return ``page_count`` and ``pages`` of ``{page, markdown}``.

    Empty pages are omitted. ``page`` is 1-based.
    """
    language = _ocr_language_code(ocr_language)
    if not pdf_bytes.startswith(b"%PDF"):
        raise InvalidPdf("file is not a PDF")
    _require_language_files(language)

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
            ocr_function=ocr_page,
            show_progress=False,
        )
        if not isinstance(chunks, list):
            raise RuntimeError("PyMuPDF4LLM did not return one chunk per page")
        pictures = cut_embedded_pictures(document)
    finally:
        document.close()

    return {
        "page_count": page_count,
        "pages": _non_empty_pages(chunks),
        "pictures": pictures,
    }


def ocr_page(page, dpi=150, language="eng", keep_ocr_text=False, pixmap=None):
    """Recognize one page. PyMuPDF4LLM calls this only for a page that needs OCR."""
    os.environ["TESSDATA_PREFIX"] = str(tessdata_dir())
    from pymupdf4llm.ocr.tesseract_api import exec_ocr

    return exec_ocr(
        page,
        dpi=dpi,
        pixmap=pixmap,
        language=language,
        keep_ocr_text=keep_ocr_text,
    )


def installed_ocr_languages() -> list[str]:
    """Language codes whose dictionary files are installed."""
    root = tessdata_dir()
    codes: list[str] = []
    for path in root.rglob("*.traineddata"):
        codes.append(path.relative_to(root).as_posix().removesuffix(".traineddata"))
    return sorted(codes)


def tessdata_dir() -> Path:
    configured = os.environ.get("TESSDATA_PREFIX")
    if configured:
        path = Path(configured)
    else:
        try:
            found = pymupdf.get_tessdata()
        except RuntimeError as exc:
            raise OcrUnavailable("Tesseract language data is not installed") from exc
        path = Path(found) if found else None
    if path is None or not (path / "eng.traineddata").is_file():
        raise OcrUnavailable("Tesseract language data is not installed")
    return path


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
    parts = language.split("+")
    if not parts or any(_LANGUAGE_PART.fullmatch(part) is None for part in parts):
        raise UnsupportedOcrLanguage(
            "ocr_language must be Tesseract codes such as eng, deu, chi_sim, or eng+rus"
        )
    return "+".join(parts)


def _require_language_files(language: str) -> None:
    root = tessdata_dir()
    missing = [part for part in language.split("+") if not _language_file(root, part).is_file()]
    if missing:
        raise UnsupportedOcrLanguage("no language data for " + ", ".join(missing))


def _language_file(root: Path, part: str) -> Path:
    path = root.joinpath(*part.split("/")).with_suffix(".traineddata")
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsupportedOcrLanguage(
            "ocr_language must be Tesseract codes such as eng, deu, chi_sim, or eng+rus"
        )
    return path


def _page_number(chunk: dict[str, Any]) -> int | None:
    metadata = chunk.get("metadata") or {}
    for key in ("page_number", "page"):
        value = metadata.get(key)
        if isinstance(value, int) and value >= 1:
            return value
    return None
