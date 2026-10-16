"""Embedded raster pictures are optional, listed per occurrence, then fetched by id."""

from __future__ import annotations

import asyncio
import threading
import time

import pymupdf
import pymupdf4llm
from fastapi.testclient import TestClient

from pymupdf4llm_service.app import app
from pymupdf4llm_service.pdf_to_page_markdown import pdf_to_page_markdown
from pymupdf4llm_service.pictures import (
    PictureStore,
    cut_embedded_pictures,
    empty_picture_report,
    picture_store,
)


def _raster_png(width: int = 120, height: int = 140, red: int = 10) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 0)
    pixmap.set_rect(pixmap.irect, (red, 20, 200))
    return pixmap.tobytes("png")


def _pdf_with_rasters(count: int = 1) -> bytes:
    document = pymupdf.open()
    for index in range(count):
        page = document.new_page()
        page.insert_image(
            pymupdf.Rect(0, 0, 120, 140),
            stream=_raster_png(red=10 + index * 40),
        )
    payload = document.tobytes()
    document.close()
    return payload


def _pdf_with_repeated_logo() -> bytes:
    png = _raster_png()
    document = pymupdf.open()
    first = document.new_page()
    first.insert_image(pymupdf.Rect(0, 0, 120, 140), stream=png)
    xref = int(first.get_images()[0][0])
    second = document.new_page()
    second.insert_image(pymupdf.Rect(0, 0, 120, 140), xref=xref)
    assert int(second.get_images()[0][0]) == xref
    payload = document.tobytes()
    document.close()
    return payload


def _vector_only_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.draw_rect(pymupdf.Rect(20, 20, 200, 180), color=(1, 0, 0), fill=(0, 0, 1), width=3)
    page.insert_text((72, 72), "Valve diagram")
    payload = document.tobytes()
    document.close()
    return payload


def _open(payload: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=payload, filetype="pdf")


def test_convert_without_the_flag_does_not_cut_pictures(monkeypatch) -> None:
    def refuse(_document):
        raise AssertionError("picture extraction must not run")

    monkeypatch.setattr("pymupdf4llm_service.pdf_to_page_markdown.cut_embedded_pictures", refuse)
    client = TestClient(app)
    response = client.post(
        "/v1/convert",
        files={"file": ("manual.pdf", _pdf_with_rasters(), "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pictures"] == []
    assert body["picture_report"]["requested"] is False
    assert body["picture_report"]["scope"] == "embedded_raster"
    assert body["picture_report"]["listed"] == 0


def test_convert_lists_a_picture_and_get_returns_png() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/convert",
        files={"file": ("manual.pdf", _pdf_with_rasters(), "application/pdf")},
        data={"include_pictures": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    pictures = body["pictures"]
    assert len(pictures) == 1
    assert pictures[0]["page"] == 1
    assert pictures[0]["width"] == 120
    assert pictures[0]["height"] == 140
    assert body["picture_report"]["requested"] is True
    assert body["picture_report"]["truncated"] is False
    assert body["picture_report"]["listed"] == 1
    assert body["picture_report"]["scope"] == "embedded_raster"
    fetched = client.get(f"/v1/pictures/{pictures[0]['id']}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/png")
    assert fetched.content.startswith(b"\x89PNG")


def test_repeated_logo_can_be_fetched_from_both_pages() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/convert",
        files={"file": ("logo.pdf", _pdf_with_repeated_logo(), "application/pdf")},
        data={"include_pictures": "true"},
    )
    assert response.status_code == 200
    pictures = response.json()["pictures"]
    assert [item["page"] for item in pictures] == [1, 2]
    assert pictures[0]["id"] != pictures[1]["id"]
    first = client.get(f"/v1/pictures/{pictures[0]['id']}")
    second = client.get(f"/v1/pictures/{pictures[1]['id']}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == second.content
    assert response.json()["picture_report"]["listed"] == 2


def test_vector_drawing_is_not_an_embedded_picture() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/convert",
        files={"file": ("diagram.pdf", _vector_only_pdf(), "application/pdf")},
        data={"include_pictures": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pictures"] == []
    assert body["picture_report"]["listed"] == 0
    assert body["picture_report"]["skipped"] == 0
    assert body["picture_report"]["truncated"] is False
    assert "Valve diagram" in body["pages"][0]["markdown"]


def test_missing_picture_is_not_found() -> None:
    client = TestClient(app)
    response = client.get("/v1/pictures/not-a-real-picture")
    assert response.status_code == 404


def test_encoded_size_skips_decode(monkeypatch) -> None:
    monkeypatch.setattr(
        "pymupdf4llm_service.pictures._stream_length",
        lambda _document, _xref: 20_000_000,
    )

    def refuse(_document, _xref: int) -> bytes:
        raise AssertionError("pixel decode must not run")

    monkeypatch.setattr("pymupdf4llm_service.pictures._png_bytes", refuse)
    document = _open(_pdf_with_rasters())
    try:
        cut = cut_embedded_pictures(document)
    finally:
        document.close()
    assert cut["pictures"] == []
    assert cut["report"]["reasons"]["encoded_size"] == 1
    assert cut["report"]["truncated"] is True


def test_each_limit_reports_a_reason(monkeypatch) -> None:
    document = _open(_pdf_with_rasters(2))
    try:
        monkeypatch.setenv("MAX_PICTURES", "1")
        counted = cut_embedded_pictures(document)
        assert counted["report"]["listed"] == 1
        assert counted["report"]["reasons"]["count"] == 1
        picture_store.release_many([item["id"] for item in counted["pictures"]])

        monkeypatch.setenv("MAX_PICTURE_PNG_BYTES", "1000")
        decoded = cut_embedded_pictures(document)
        assert decoded["pictures"] == []
        assert decoded["report"]["reasons"]["decoded_size"] == 2

        monkeypatch.delenv("MAX_PICTURE_PNG_BYTES", raising=False)
        monkeypatch.setattr(
            "pymupdf4llm_service.pictures._png_bytes",
            lambda _document, _xref: None,
        )
        unreadable = cut_embedded_pictures(document)
        assert unreadable["report"]["reasons"]["unreadable"] == 2
    finally:
        document.close()


def test_mixed_pdf_returns_one_picture_and_skips_the_other(monkeypatch) -> None:
    calls = {"n": 0}

    def length(document: pymupdf.Document, xref: int) -> int | None:
        kind, value = document.xref_get_key(xref, "Length")
        if calls["n"] >= 1:
            return 20_000_000
        calls["n"] += 1
        if kind != "int":
            return None
        return int(value)

    monkeypatch.setattr("pymupdf4llm_service.pictures._stream_length", length)
    document = _open(_pdf_with_rasters(2))
    try:
        cut = cut_embedded_pictures(document)
    finally:
        document.close()
    assert len(cut["pictures"]) == 1
    assert cut["report"]["listed"] == 1
    assert cut["report"]["skipped"] == 1
    assert cut["report"]["reasons"]["encoded_size"] == 1
    assert cut["report"]["truncated"] is True
    picture_store.release_many([item["id"] for item in cut["pictures"]])


def test_storage_rejection_names_the_omitted_picture(monkeypatch) -> None:
    monkeypatch.setattr(
        "pymupdf4llm_service.pictures.picture_store.put",
        lambda _png: None,
    )
    document = _open(_pdf_with_rasters())
    try:
        cut = cut_embedded_pictures(document)
    finally:
        document.close()
    assert cut["pictures"] == []
    assert cut["report"]["skipped"] == 1
    assert cut["report"]["reasons"]["storage"] == 1
    assert cut["report"]["truncated"] is True


def test_timeout_is_checked_between_pictures(monkeypatch) -> None:
    calls = {"n": 0}
    real_png = __import__(
        "pymupdf4llm_service.pictures", fromlist=["_png_bytes"]
    )._png_bytes

    def slow(document, xref: int):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.05)
        return real_png(document, xref)

    monkeypatch.setenv("PICTURE_EXTRACT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr("pymupdf4llm_service.pictures._png_bytes", slow)
    document = _open(_pdf_with_rasters(2))
    try:
        cut = cut_embedded_pictures(document)
    finally:
        document.close()
    assert calls["n"] == 1
    assert cut["report"]["listed"] == 1
    assert cut["report"]["reasons"]["time"] == 1
    picture_store.release_many([item["id"] for item in cut["pictures"]])


def test_failed_conversion_drops_stored_pictures(monkeypatch) -> None:
    stored: list[str] = []

    def cut_then_fail(_document):
        picture_id = picture_store.put(b"\x89PNG\r\n")
        assert picture_id is not None
        stored.append(picture_id)
        raise RuntimeError("cut failed")

    monkeypatch.setattr(
        "pymupdf4llm_service.pdf_to_page_markdown._require_language_files",
        lambda _language: None,
    )
    monkeypatch.setattr(
        pymupdf4llm,
        "to_markdown",
        lambda *_args, **_kwargs: [{"metadata": {"page_number": 1}, "text": "Pump"}],
    )
    monkeypatch.setattr(
        "pymupdf4llm_service.pdf_to_page_markdown.cut_embedded_pictures",
        cut_then_fail,
    )
    try:
        pdf_to_page_markdown(_pdf_with_rasters(), include_pictures=True)
        raise AssertionError("conversion should fail")
    except RuntimeError:
        pass
    assert stored
    assert picture_store.get(stored[0]) is None


def test_cancelled_picture_read_keeps_the_convert_slot(monkeypatch) -> None:
    """Picture cutting runs inside the same shielded conversion as the text."""
    current = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow(_pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        assert release.wait(5)
        with lock:
            current -= 1
        return {
            "page_count": 1,
            "pages": [{"page": 1, "markdown": "ok"}],
            "pictures": [],
            "picture_report": empty_picture_report(requested=include_pictures),
        }

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                    data={"include_pictures": "true"},
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
                    data={"include_pictures": "true"},
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


def test_cancelled_conversion_drops_picture_ids(monkeypatch) -> None:
    stored: list[str] = []
    release = threading.Event()

    def slow(_pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        picture_id = picture_store.put(b"\x89PNG\r\n")
        assert picture_id is not None
        stored.append(picture_id)
        assert release.wait(5)
        return {
            "page_count": 1,
            "pages": [{"page": 1, "markdown": "ok"}],
            "pictures": [
                {
                    "id": picture_id,
                    "page": 1,
                    "index_on_page": 0,
                    "width": 120,
                    "height": 140,
                }
            ],
            "picture_report": empty_picture_report(requested=True),
        }

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                    data={"include_pictures": "true"},
                )
            )
            for _ in range(50):
                if stored:
                    break
                await asyncio.sleep(0.05)
            assert stored
            first.cancel()
            await asyncio.wait([first], timeout=2)
            release.set()
            await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert picture_store.get(stored[0]) is None


def test_store_round_trip_expiration_and_concurrent_reads() -> None:
    store = PictureStore(max_bytes=10)
    picture_id = store.put(b"12345")
    assert picture_id is not None
    assert store.get(picture_id) == b"12345"
    assert store.get(picture_id) is None
    assert store.put(b"123456") is not None
    assert store.put(b"123456") is None

    shared = PictureStore(max_bytes=100)
    first = shared.put(b"logo")
    assert first is not None
    second = shared.alias(first)
    assert second is not None
    assert second != first
    assert shared.stats()["bytes"] == 4
    found: list[bytes | None] = []

    def read(picture: str) -> None:
        found.append(shared.get(picture))

    threads = [threading.Thread(target=read, args=(item,)) for item in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(item for item in found if item is not None) == [b"logo", b"logo"]

    raced = PictureStore()
    same = raced.put(b"once")
    assert same is not None
    winners: list[bytes | None] = []

    def race() -> None:
        winners.append(raced.get(same))

    pair = [threading.Thread(target=race) for _ in range(2)]
    for thread in pair:
        thread.start()
    for thread in pair:
        thread.join()
    assert winners.count(b"once") == 1
    assert winners.count(None) == 1

    expiring = PictureStore(ttl_seconds=0.05)
    expired = expiring.put(b"gone")
    assert expired is not None
    time.sleep(0.08)
    assert expiring.get(expired) is None
    assert expiring.stats() == {"bytes": 0, "ids": 0}
