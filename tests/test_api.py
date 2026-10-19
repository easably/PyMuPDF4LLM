"""HTTP contract. The PDF reader is stubbed except for the rejection checks."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
from fastapi.testclient import TestClient

from pymupdf4llm_service.app import AGPL_SOURCE_URL, _admission, app


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
    def fake_reader(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
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


def test_picture_text_repair_flag_is_forwarded(monkeypatch) -> None:
    def fake_reader(
        pdf_bytes: bytes,
        *,
        ocr_language: str = "eng",
        include_pictures: bool = False,
        repair_picture_text: bool = False,
    ) -> dict:
        assert pdf_bytes.startswith(b"%PDF")
        assert repair_picture_text is True
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "repaired"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", fake_reader)
    response = TestClient(app).post(
        "/v1/convert",
        files={"file": ("manual.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"repair_picture_text": "true"},
    )

    assert response.status_code == 200
    assert response.json()["pages"][0]["markdown"] == "repaired"


def test_health_answers_while_a_conversion_runs(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        started.set()
        assert release.wait(5)
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            for _ in range(50):
                if started.is_set():
                    break
                await asyncio.sleep(0.05)
            assert started.is_set()
            health = await asyncio.wait_for(client.get("/health"), timeout=2)
            assert health.status_code == 200
            release.set()
            response = await task
            assert response.status_code == 200

    worker = threading.Thread(target=lambda: asyncio.run(scenario()))
    worker.start()
    worker.join(8)
    if worker.is_alive():
        release.set()
        worker.join(2)
        raise AssertionError("health did not answer while a conversion was running")


def test_one_conversion_runs_at_a_time(monkeypatch) -> None:
    current = 0
    peak = 0
    lock = threading.Lock()

    def slow(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.3)
        with lock:
            current -= 1
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            second = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("b.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            done = await asyncio.gather(first, second)
        assert [item.status_code for item in done] == [200, 200]
        assert peak == 1

    asyncio.run(scenario())


def test_a_request_past_the_queue_is_rejected(monkeypatch) -> None:
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def slow(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        nonlocal calls
        with calls_lock:
            calls += 1
        assert release.wait(5)
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = [
                asyncio.create_task(
                    client.post(
                        "/v1/convert",
                        files={"file": (f"{index}.pdf", b"%PDF-1.4", "application/pdf")},
                    )
                )
                for index in range(3)
            ]
            rejected = None
            started = 0
            for _ in range(40):
                finished = [task for task in tasks if task.done()]
                rejected = next(
                    (task.result() for task in finished if task.result().status_code == 429),
                    None,
                )
                with calls_lock:
                    started = calls
                if rejected is not None and started >= 1:
                    break
                await asyncio.sleep(0.05)
            assert rejected is not None
            assert rejected.json()["detail"] == "too many PDFs are already waiting"
            assert started == 1
            release.set()
            responses = await asyncio.gather(*tasks)
        assert sorted(item.status_code for item in responses) == [200, 200, 429]
        assert calls == 2

    asyncio.run(scenario())


def test_cancelled_request_keeps_the_slot_until_the_thread_finishes(monkeypatch) -> None:
    current = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        assert release.wait(5)
        with lock:
            current -= 1
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
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
            with lock:
                assert current == 1
            first.cancel()
            await asyncio.wait([first], timeout=2)
            assert first.done()
            await asyncio.sleep(0.2)
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
        assert peak == 1

    asyncio.run(scenario())


def test_cancelled_conversion_still_fills_the_queue(monkeypatch) -> None:
    calls = 0
    calls_lock = threading.Lock()
    release = threading.Event()

    def slow(pdf_bytes: bytes, *, ocr_language: str = "eng", include_pictures: bool = False) -> dict:
        nonlocal calls
        with calls_lock:
            calls += 1
        assert release.wait(5)
        return {"page_count": 1, "pages": [{"page": 1, "markdown": "ok"}]}

    monkeypatch.setattr("pymupdf4llm_service.app.pdf_to_page_markdown", slow)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            for _ in range(50):
                with calls_lock:
                    started = calls
                if started == 1:
                    break
                await asyncio.sleep(0.05)
            assert started == 1
            second = asyncio.create_task(
                client.post(
                    "/v1/convert",
                    files={"file": ("b.pdf", b"%PDF-1.4", "application/pdf")},
                )
            )
            for _ in range(50):
                if _admission.admitted == 2:
                    break
                await asyncio.sleep(0.05)
            assert _admission.admitted == 2
            with calls_lock:
                assert calls == 1
            first.cancel()
            await asyncio.wait([first], timeout=2)
            assert first.done()
            assert _admission.admitted == 2
            third = await client.post(
                "/v1/convert",
                files={"file": ("c.pdf", b"%PDF-1.4", "application/pdf")},
            )
            assert third.status_code == 429
            assert third.json()["detail"] == "too many PDFs are already waiting"
            with calls_lock:
                assert calls == 1
            release.set()
            second_response = await asyncio.wait_for(second, timeout=5)
        assert second_response.status_code == 200
        with calls_lock:
            assert calls == 2

    asyncio.run(scenario())
