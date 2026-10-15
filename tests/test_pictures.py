"""Embedded pictures are cut in the same PDF open as the text, then fetched by id."""

from __future__ import annotations

import asyncio
import io

import pymupdf
from PIL import Image
from fastapi.testclient import TestClient

from pymupdf4llm_service.app import app
from pymupdf4llm_service.pictures import PictureStore, cut_embedded_pictures, picture_store


def _pdf_with_jpeg(width: int = 120, height: int = 140) -> bytes:
    image = Image.new("RGB", (width, height), (10, 20, 200))
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(0, 0, width, height), stream=encoded.getvalue())
    payload = document.tobytes()
    document.close()
    return payload


def test_convert_lists_a_picture_and_get_returns_png() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/convert",
        files={"file": ("manual.pdf", _pdf_with_jpeg(), "application/pdf")},
    )
    assert response.status_code == 200
    pictures = response.json()["pictures"]
    assert len(pictures) == 1
    assert pictures[0]["page"] == 1
    assert pictures[0]["width"] == 120
    assert pictures[0]["height"] == 140
    fetched = client.get(f"/v1/pictures/{pictures[0]['id']}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/png")
    assert fetched.content.startswith(b"\x89PNG")


def test_missing_picture_is_not_found() -> None:
    client = TestClient(app)
    response = client.get("/v1/pictures/not-a-real-picture")
    assert response.status_code == 404


def test_reported_stream_length_skips_decode(monkeypatch) -> None:
    monkeypatch.setattr(
        "pymupdf4llm_service.pictures._stream_length",
        lambda _document, _xref: 20_000_000,
    )

    def refuse(_document, _xref: int) -> bytes:
        raise AssertionError("pixel decode must not run")

    monkeypatch.setattr("pymupdf4llm_service.pictures._png_bytes", refuse)
    document = pymupdf.open(stream=_pdf_with_jpeg(), filetype="pdf")
    try:
        pictures = cut_embedded_pictures(document)
    finally:
        document.close()
    assert pictures == []


def test_cancelled_picture_read_keeps_the_convert_slot(monkeypatch) -> None:
    """Picture cutting runs inside the same shielded conversion as the text."""
    import threading

    current = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow(_pdf_bytes: bytes, *, ocr_language: str = "eng") -> dict:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        assert release.wait(5)
        with lock:
            current -= 1
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}], "pictures": []}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            for _ in range(50):
                with lock:
                    if current == 1:
                        break
                await asyncio.sleep(0.05)
            first.cancel()
            await asyncio.wait([first], timeout=2)
            second = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("b.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            await asyncio.sleep(0.2)
            with lock:
                assert current == 1
                assert peak == 1
            release.set()
            response = await asyncio.wait_for(second, timeout=5)
        assert response.status_code == 200

    asyncio.run(scenario())


def test_store_round_trip() -> None:
    store = PictureStore(max_bytes=10)
    picture_id = store.put(b"12345")
    assert picture_id is not None
    assert store.get(picture_id) == b"12345"
    assert store.get(picture_id) is None
    assert store.put(b"123456") is not None
    assert store.put(b"123456") is None
