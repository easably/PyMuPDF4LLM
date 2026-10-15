"""HTTP entry: one PDF in, markdown per page out.

Callers reach this process over the network. They do not import PyMuPDF4LLM.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from pymupdf4llm_service.pdf_to_page_markdown import (
    InvalidPdf,
    OcrUnavailable,
    UnsupportedOcrLanguage,
    pdf_to_page_markdown,
)

AGPL_SOURCE_URL = "https://github.com/easably/PyMuPDF4LLM"
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(150 * 1024 * 1024)))
_CONVERT_LIMIT = int(os.environ.get("MAX_CONCURRENT_CONVERTS", "1"))
_QUEUE_LIMIT = int(os.environ.get("MAX_QUEUED_CONVERTS", "1"))
if _CONVERT_LIMIT < 1:
    raise RuntimeError("MAX_CONCURRENT_CONVERTS must be at least 1")
if _QUEUE_LIMIT < 0:
    raise RuntimeError("MAX_QUEUED_CONVERTS must be at least 0")

app = FastAPI(title="PyMuPDF4LLM text service", version="0.1.0")


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
    try:
        return await call_next(request)
    finally:
        await _release_admission()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "license": "AGPL-3.0", "source": AGPL_SOURCE_URL}


@app.post("/v1/convert")
async def convert_pdf_to_page_markdown(
    file: UploadFile = File(...),
    ocr_language: str = Form("eng"),
    force_ocr: str = Form("false"),
) -> dict:
    if _form_flag_is_true(force_ocr):
        raise HTTPException(
            status_code=400,
            detail="force_ocr is not available; scans are recognized per page",
        )
    try:
        # The file is read only after a conversion slot is free, so waiting
        # requests do not each hold a full PDF in memory.
        assert _admission.slots is not None
        async with _admission.slots:
            pdf_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(pdf_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="PDF exceeds the upload limit")
            if not pdf_bytes:
                raise HTTPException(status_code=400, detail="empty file")
            return await asyncio.to_thread(
                pdf_to_page_markdown, pdf_bytes, ocr_language=ocr_language
            )
    except InvalidPdf as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnsupportedOcrLanguage as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OcrUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _form_flag_is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def _admit() -> bool:
    assert _admission.lock is not None
    async with _admission.lock:
        if _admission.admitted >= _CONVERT_LIMIT + _QUEUE_LIMIT:
            return False
        _admission.admitted += 1
        return True


async def _release_admission() -> None:
    assert _admission.lock is not None
    async with _admission.lock:
        _admission.admitted -= 1
