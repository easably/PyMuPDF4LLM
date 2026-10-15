"""HTTP entry: one PDF in, markdown per page out.

Callers reach this process over the network. They do not import PyMuPDF4LLM.
"""

from __future__ import annotations

import asyncio
import os
import threading

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from pymupdf4llm_service.pdf_to_page_markdown import (
    InvalidPdf,
    OcrUnavailable,
    UnsupportedOcrLanguage,
    pdf_to_page_markdown,
)
from pymupdf4llm_service.pictures import picture_store

AGPL_SOURCE_URL = "https://github.com/easably/PyMuPDF4LLM"
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(150 * 1024 * 1024)))
_CONVERT_LIMIT = int(os.environ.get("MAX_CONCURRENT_CONVERTS", "1"))
_QUEUE_LIMIT = int(os.environ.get("MAX_QUEUED_CONVERTS", "1"))
if _CONVERT_LIMIT < 1:
    raise RuntimeError("MAX_CONCURRENT_CONVERTS must be at least 1")
if _QUEUE_LIMIT < 0:
    raise RuntimeError("MAX_QUEUED_CONVERTS must be at least 0")

app = FastAPI(title="PyMuPDF4LLM text service", version="0.2.1")


class _InFlight:
    """One accepted upload. Its queue place lasts until the thread is gone."""

    def __init__(self) -> None:
        self.handed_off = False
        self.thread_done = False
        self.request_done = False
        self.admission_released = False


class _Admission:
    """Running and waiting conversions. Recreated when the event loop changes."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self.slots: asyncio.Semaphore | None = None
        self.lock: asyncio.Lock | None = None
        self.admitted = 0

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self.slots = asyncio.Semaphore(_CONVERT_LIMIT)
        self.lock = asyncio.Lock()
        self.admitted = 0


_admission = _Admission()


@app.middleware("http")
async def reject_when_the_queue_is_full(request: Request, call_next):
    """Stop a new upload before it is kept for conversion."""
    if request.method != "POST" or request.url.path != "/v1/convert":
        return await call_next(request)
    _admission.bind()
    if not await _admit():
        return JSONResponse(
            status_code=429,
            content={"detail": "too many PDFs are already waiting"},
            headers={"Retry-After": "1"},
        )
    flight = _InFlight()
    request.state.conversion = flight
    try:
        return await call_next(request)
    finally:
        await _finish_request(flight)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "license": "AGPL-3.0", "source": AGPL_SOURCE_URL}


@app.get("/v1/pictures/{picture_id}")
async def fetch_picture(picture_id: str) -> Response:
    if not picture_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=404, detail="picture not found")
    png = picture_store.get(picture_id)
    if png is None:
        raise HTTPException(status_code=404, detail="picture not found")
    return Response(content=png, media_type="image/png")


@app.post("/v1/convert")
async def convert_pdf_to_page_markdown(
    request: Request,
    file: UploadFile = File(...),
    ocr_language: str = Form("eng"),
    force_ocr: str = Form("false"),
) -> dict:
    if _form_flag_is_true(force_ocr):
        raise HTTPException(
            status_code=400,
            detail="force_ocr is not available; scans are recognized per page",
        )
    flight: _InFlight = request.state.conversion
    _admission.bind()
    slots = _admission.slots
    assert slots is not None
    await slots.acquire()
    # A cancelled request must not give this slot, or its queue place, to the
    # next PDF while the worker thread is still inside the conversion.
    try:
        pdf_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(pdf_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="PDF exceeds the upload limit")
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="empty file")
        flight.handed_off = True
        return await asyncio.shield(_convert_and_release(slots, flight, pdf_bytes, ocr_language))
    except InvalidPdf as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnsupportedOcrLanguage as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OcrUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if not flight.handed_off:
            slots.release()


def _form_flag_is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def _admit() -> bool:
    assert _admission.lock is not None
    async with _admission.lock:
        if _admission.admitted >= _CONVERT_LIMIT + _QUEUE_LIMIT:
            return False
        _admission.admitted += 1
        return True


async def _finish_request(flight: _InFlight) -> None:
    assert _admission.lock is not None
    async with _admission.lock:
        flight.request_done = True
        _release_admission_if_idle(flight)


async def _finish_thread(flight: _InFlight) -> None:
    assert _admission.lock is not None
    async with _admission.lock:
        flight.thread_done = True
        _release_admission_if_idle(flight)


def _release_admission_if_idle(flight: _InFlight) -> None:
    """Drop the queue place once the request and its thread are both finished.

    Caller holds the admission lock. A disconnect keeps the place while the
    thread is still converting, so a new upload cannot take that waiting spot.
    """
    if flight.admission_released:
        return
    if flight.request_done and (not flight.handed_off or flight.thread_done):
        flight.admission_released = True
        _admission.admitted -= 1


async def _convert_and_release(
    slots: asyncio.Semaphore,
    flight: _InFlight,
    pdf_bytes: bytes,
    ocr_language: str,
) -> dict:
    """Run the conversion on a thread and release ``slots`` when that thread ends."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict] = loop.create_future()

    def work() -> None:
        error: BaseException | None = None
        result: dict | None = None
        try:
            result = pdf_to_page_markdown(pdf_bytes, ocr_language=ocr_language)
        except BaseException as exc:
            error = exc
        loop.call_soon_threadsafe(_deliver_conversion, future, slots, flight, result, error)

    try:
        threading.Thread(target=work, name="pdf-convert", daemon=True).start()
    except BaseException:
        flight.handed_off = False
        raise
    return await future


def _deliver_conversion(
    future: asyncio.Future[dict],
    slots: asyncio.Semaphore,
    flight: _InFlight,
    result: dict | None,
    error: BaseException | None,
) -> None:
    try:
        if not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                assert result is not None
                future.set_result(result)
    finally:
        slots.release()
        asyncio.get_running_loop().create_task(_finish_thread(flight))
