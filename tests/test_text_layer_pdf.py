"""One real text-layer PDF through PyMuPDF4LLM. Those pages must not need OCR."""

from __future__ import annotations

from pymupdf4llm_service.pdf_to_page_markdown import pdf_to_page_markdown
from tests.text_layer_pdf import text_layer_pdf


def test_text_layer_pdf_returns_the_page_text() -> None:
    pdf_bytes = text_layer_pdf(
        "Maintenance interval 10 hours",
        "Maintenance interval 500 hours",
    )

    result = pdf_to_page_markdown(pdf_bytes, ocr_language="eng")

    assert result["page_count"] == 2
    text = "\n".join(page["markdown"] for page in result["pages"])
    assert "10 hours" in text
    assert "500 hours" in text
    assert [page["page"] for page in result["pages"]] == [1, 2]
