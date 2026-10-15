"""HTTP contract. The PDF reader is stubbed except for the rejection checks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from pymupdf4llm_service.app import AGPL_SOURCE_URL, app


def test_health_names_the_agpl_source() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["license"] == "AGPL-3.0"
    assert body["source"] == AGPL_SOURCE_URL


def test_force_ocr_is_rejected(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("the PDF reader must not run")

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", fail)
    response = TestClient(app).post(
        "/v1/convert",
        files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
        data={"force_ocr": "true"},
    )
    assert response.status_code == 400


def test_non_pdf_is_rejected() -> None:
    response = TestClient(app).post(
        "/v1/convert",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_convert_returns_non_empty_pages(monkeypatch) -> None:
    def fake_reader(pdf_bytes: bytes, *, ocr_language: str = "eng") -> dict:
        assert pdf_bytes.startswith(b"%PDF")
        assert ocr_language == "rus"
        return {
            "page_count": 2,
            "pages": [{"page": 1, "markdown": "Pump oil"}],
        }

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", fake_reader)
    response = TestClient(app).post(
        "/v1/convert",
        files={"file": ("manual.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"ocr_language": "rus"},
    )
    assert response.status_code == 200
    assert response.json()["pages"] == [{"page": 1, "markdown": "Pump oil"}]
