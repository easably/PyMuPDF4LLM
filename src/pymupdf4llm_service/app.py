"""HTTP entry: one PDF in, markdown per page out.

Callers reach this process over the network. They do not import PyMuPDF4LLM.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from pymupdf4llm_service.pdf_to_page_markdown import (
    InvalidPdf,
    OcrUnavailable,
    UnsupportedOcrLanguage,
    pdf_to_page_markdown,
)

AGPL_SOURCE_URL = "https://github.com/easably/PyMuPDF4LLM"
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(150 * 1024 * 1024)))
_CONVERT_LIMIT = int(os.environ.get("MAX_CONCURRENT_CONVERTS", "1"))
if _CONVERT_LIMIT < 1:
    raise RuntimeError("MAX_CONCURRENT_CONVERTS must be at least 1")

app = FastAPI(title="PyMuPDF4LLM text service", version="0.1.0")
_convert_slots = asyncio.Semaphore(_CONVERT_LIMIT)


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
    pdf_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the upload limit")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        # Off the request loop, and only a few at once, so /health still answers.
        async with _convert_slots:
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
