"""Cut embedded pictures while the PDF is already open for text.

The convert JSON lists each picture by id. The bytes stay here until the
caller fetches them. A picture is skipped before pixel decode when its
compressed stream or pixel size is over the budget, or when that size
cannot be measured.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any

import pymupdf

_STORE_TTL_SECONDS = 600.0
_MIN_SIDE = 100


class PictureStore:
    """PNG bytes kept until the caller fetches them.

    ``get`` removes the bytes. The store never holds more than ``max_bytes``
    across every PDF. Unfetched pictures still expire after ten minutes.
    """

    def __init__(self, max_bytes: int | None = None) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[bytes, float]] = {}
        self._max_bytes = (
            max_bytes
            if max_bytes is not None
            else _positive_int("MAX_PICTURE_STORE_BYTES", 64 * 1024 * 1024)
        )

    def put(self, png: bytes) -> str | None:
        if len(png) > self._max_bytes:
            return None
        picture_id = secrets.token_urlsafe(16)
        with self._lock:
            self._sweep_locked()
            if self._byte_count_locked() + len(png) > self._max_bytes:
                return None
            self._items[picture_id] = (png, time.monotonic() + _STORE_TTL_SECONDS)
        return picture_id

    def get(self, picture_id: str) -> bytes | None:
        with self._lock:
            self._sweep_locked()
            item = self._items.pop(picture_id, None)
        if item is None:
            return None
        return item[0]

    def _byte_count_locked(self) -> int:
        return sum(len(png) for png, _deadline in self._items.values())

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, (_, deadline) in self._items.items() if deadline <= now]
        for key in expired:
            del self._items[key]


def cut_embedded_pictures(document: pymupdf.Document) -> list[dict[str, Any]]:
    """Return picture metadata. Bytes are stored under ``id`` for a later GET."""
    max_images = _positive_int("MAX_PICTURES", 80)
    max_stream_bytes = _positive_int("MAX_PICTURE_STREAM_BYTES", 8 * 1024 * 1024)
    max_png_bytes = _positive_int("MAX_PICTURE_PNG_BYTES", 64 * 1024 * 1024)
    deadline_seconds = _positive_float("PICTURE_EXTRACT_TIMEOUT_SECONDS", 20.0)
    started = time.monotonic()
    png_total = 0
    pictures: list[dict[str, Any]] = []
    stored_xref: dict[int, dict[str, Any]] = {}

    for page_index, page in enumerate(document):
        if time.monotonic() - started >= deadline_seconds or len(pictures) >= max_images:
            break
        smasks = {item[1] for item in page.get_images(full=True) if item[1]}
        index_on_page = 0
        for item in page.get_images(full=True):
            if time.monotonic() - started >= deadline_seconds or len(pictures) >= max_images:
                break
            xref = int(item[0])
            if xref in smasks:
                continue
            width = int(item[2])
            height = int(item[3])
            if width < _MIN_SIDE or height < _MIN_SIDE:
                continue
            cached = stored_xref.get(xref)
            if cached is not None:
                pictures.append({**cached, "page": page_index + 1, "index_on_page": index_on_page})
                index_on_page += 1
                continue
            if width * height * 4 > max_png_bytes - png_total:
                continue
            stream_length = _stream_length(document, xref)
            if stream_length is None or stream_length > max_stream_bytes:
                continue
            png = _png_bytes(document, xref)
            if png is None or png_total + len(png) > max_png_bytes:
                continue
            picture_id = picture_store.put(png)
            if picture_id is None:
                continue
            meta = {
                "id": picture_id,
                "page": page_index + 1,
                "index_on_page": index_on_page,
                "width": width,
                "height": height,
            }
            stored_xref[xref] = meta
            pictures.append(meta)
            png_total += len(png)
            index_on_page += 1
    return pictures


def _stream_length(document: pymupdf.Document, xref: int) -> int | None:
    kind, value = document.xref_get_key(xref, "Length")
    if kind != "int":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _png_bytes(document: pymupdf.Document, xref: int) -> bytes | None:
    try:
        extracted = document.extract_image(xref)
        pixmap = pymupdf.Pixmap(extracted["image"])
        return pixmap.tobytes("png")
    except Exception:
        return None


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


picture_store = PictureStore()
