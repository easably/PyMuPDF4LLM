"""A real mixed PDF: selectable text, plus one scan page that goes through OCR."""

from __future__ import annotations

from pymupdf4llm_service.pdf_to_page_markdown import ocr_page, pdf_to_page_markdown
from tests.text_layer_pdf import mixed_pdf, text_and_scan_same_page


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


def test_page_with_selectable_text_does_not_ocr_its_picture(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("Tesseract must not run on a page with selectable text")

    monkeypatch.setattr("pymupdf4llm.ocr.tesseract_api.exec_ocr", fail)
    result = pdf_to_page_markdown(
        text_and_scan_same_page("SELECTABLE CAPTION", "IMAGE TEXT 4821"),
    )

    assert "SELECTABLE CAPTION" in result["pages"][0]["markdown"]
    assert "IMAGE TEXT 4821" not in result["pages"][0]["markdown"]


def test_russian_scan_uses_the_requested_language(monkeypatch) -> None:
    calls = _spy_on_ocr(monkeypatch)
    result = pdf_to_page_markdown(
        mixed_pdf("Maintenance interval 10 hours", "Насос масла", cjk_font=True),
        ocr_language="rus",
    )

    assert calls == [(1, "rus")]
    assert "10 hours" in result["pages"][0]["markdown"]
    assert "Насос масла" in result["pages"][1]["markdown"]


def test_german_scan_uses_the_requested_language(monkeypatch) -> None:
    calls = _spy_on_ocr(monkeypatch)
    result = pdf_to_page_markdown(
        mixed_pdf("Maintenance interval 10 hours", "Kühlmittelstand prüfen"),
        ocr_language="deu",
    )

    assert calls == [(1, "deu")]
    assert "10 hours" in result["pages"][0]["markdown"]
    assert "Kühlmittelstand prüfen" in result["pages"][1]["markdown"]


def test_chinese_scan_uses_the_requested_language(monkeypatch) -> None:
    calls = _spy_on_ocr(monkeypatch)
    result = pdf_to_page_markdown(
        mixed_pdf("Maintenance interval 10 hours", "每日保养", cjk_font=True),
        ocr_language="chi_sim",
    )

    assert calls == [(1, "chi_sim")]
    assert "10 hours" in result["pages"][0]["markdown"]
    # The markdown writer separates recognized characters with spaces.
    assert "每日保养" in "".join(result["pages"][1]["markdown"].split())
