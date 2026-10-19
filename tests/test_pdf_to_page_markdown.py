"""How page chunks become the JSON the caller stores. The reader is stubbed."""

from __future__ import annotations

import pytest

from pymupdf4llm_service.pdf_to_page_markdown import (
    InvalidPdf,
    UnsupportedOcrLanguage,
    ocr_page,
    pdf_to_page_markdown,
)
from tests.text_layer_pdf import text_layer_pdf


def test_skips_empty_pages_and_keeps_scan_text(monkeypatch) -> None:
    def fake_to_markdown(*_args, **kwargs):
        assert kwargs["pages"] == [0]
        assert kwargs["use_ocr"] is False
        assert kwargs["force_ocr"] is False
        assert kwargs["page_chunks"] is True
        assert kwargs["ocr_function"] is ocr_page
        return [
            {"metadata": {"page_number": 1}, "text": "Daily grease points"},
            {"metadata": {"page_number": 2}, "text": "   "},
            {"metadata": {"page_number": 21}, "text": "Scan of the nameplate"},
        ]

    monkeypatch.setattr(
        "pymupdf4llm_service.pdf_to_page_markdown.pymupdf4llm.to_markdown",
        fake_to_markdown,
    )
    result = pdf_to_page_markdown(text_layer_pdf("Service points"), ocr_language="eng")
    assert result["page_count"] == 1
    assert result["pages"] == [
        {"page": 1, "markdown": "Daily grease points"},
        {"page": 21, "markdown": "Scan of the nameplate"},
    ]


def test_picture_text_repair_is_opt_in(monkeypatch) -> None:
    def fake_to_markdown(*_args, **_kwargs):
        return [{"metadata": {"page_number": 1}, "text": "broken"}]

    calls = 0

    def fake_repair(_page, chunk):
        nonlocal calls
        calls += 1
        assert chunk["text"] == "broken"
        return "repaired"

    monkeypatch.setattr(
        "pymupdf4llm_service.pdf_to_page_markdown.pymupdf4llm.to_markdown",
        fake_to_markdown,
    )
    monkeypatch.setattr(
        "pymupdf4llm_service.pdf_to_page_markdown._repair_picture_text",
        fake_repair,
    )
    payload = text_layer_pdf("Service points")

    unchanged = pdf_to_page_markdown(payload)
    assert unchanged["pages"][0]["markdown"] == "broken"
    assert calls == 0

    repaired = pdf_to_page_markdown(payload, repair_picture_text=True)
    assert repaired["pages"][0]["markdown"] == "repaired"
    assert calls == 1


def test_bad_language_is_rejected() -> None:
    with pytest.raises(UnsupportedOcrLanguage):
        pdf_to_page_markdown(text_layer_pdf("Service points"), ocr_language="english")


def test_bytes_that_are_not_a_pdf_are_rejected() -> None:
    with pytest.raises(InvalidPdf):
        pdf_to_page_markdown(b"not a pdf")
