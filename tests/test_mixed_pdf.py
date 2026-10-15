"""A real mixed PDF: selectable text, plus one scan page that goes through OCR."""

from __future__ import annotations

from pymupdf4llm_service.pdf_to_page_markdown import ocr_page, pdf_to_page_markdown
from tests.text_layer_pdf import mixed_pdf


def _spy_on_ocr(monkeypatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    def spy(page, dpi=150, language="eng", keep_ocr_text=False, pixmap=None):
        calls.append((page.number, language))
        return ocr_page(
            page,
            dpi=dpi,
            language=language,
            keep_ocr_text=keep_ocr_text,
            pixmap=pixmap,
        )

    monkeypatch.setattr("pymupdf4llm_service.pdf_to_page_markdown.ocr_page", spy)
    return calls


def test_mixed_pdf_runs_ocr_only_on_the_scan(monkeypatch) -> None:
    calls = _spy_on_ocr(monkeypatch)
    result = pdf_to_page_markdown(
        mixed_pdf("Maintenance interval 10 hours", "Nameplate serial 4821"),
        ocr_language="eng",
    )

    assert calls == [(1, "eng")]
    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert "10 hours" in result["pages"][0]["markdown"]
    assert "Nameplate serial 4821" in result["pages"][1]["markdown"]


def test_russian_scan_uses_the_requested_language(monkeypatch) -> None:
    calls = _spy_on_ocr(monkeypatch)
    result = pdf_to_page_markdown(
        mixed_pdf("Maintenance interval 10 hours", "Насос масла", cyrillic_scan=True),
        ocr_language="rus",
    )

    assert calls == [(1, "rus")]
    assert "10 hours" in result["pages"][0]["markdown"]
    assert "Насос масла" in result["pages"][1]["markdown"]
