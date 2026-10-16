"""Cut embedded raster pictures while the PDF is already open for text.

The convert JSON lists each occurrence by its own id. The bytes stay here
until that id is fetched. A picture is skipped before pixel decode when its
compressed stream or pixel size is over the budget, or when that size cannot
be measured.

Vector drawings, diagrams inferred from page layout, and rendered scan pages
are outside this list. ``PICTURE_EXTRACT_TIMEOUT_SECONDS`` (default 20) is
checked between pictures. It does not interrupt a decode that has started.

Ids are transient and live only in this process. Run one service instance
unless a shared store is added on purpose. The first successful GET of an id
deletes that id. A repeated logo gets a new id per page, sharing one copy of
the bytes, so each listed occurrence can be fetched.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pymupdf

_STORE_TTL_SECONDS = 600.0
_MIN_SIDE = 100
REASON_KEYS = ("time", "count", "encoded_size", "decoded_size", "unreadable", "storage")


class PictureStore:
    """PNG bytes kept until each listed id is fetched.

    Several ids may share one PNG. ``get`` removes only that id. The store
    never holds more than ``max_bytes`` across every PDF. Unfetched ids expire
    after ``ttl_seconds`` (ten minutes by default).
    """

    def __init__(self, max_bytes: int | None = None, ttl_seconds: float = _STORE_TTL_SECONDS) -> None:
        self._lock = threading.Lock()
        self._blobs: dict[str, tuple[bytes, int]] = {}
        self._ids: dict[str, tuple[str, float]] = {}
        self._trackers: list[list[str]] = []
        self._max_bytes = (
            max_bytes
            if max_bytes is not None
            else _positive_int("MAX_PICTURE_STORE_BYTES", 64 * 1024 * 1024)
        )
        self._ttl_seconds = ttl_seconds

    def put(self, png: bytes) -> str | None:
        if len(png) > self._max_bytes:
            return None
        picture_id = secrets.token_urlsafe(16)
        blob_id = secrets.token_urlsafe(8)
        with self._lock:
            self._sweep_locked()
            if self._byte_count_locked() + len(png) > self._max_bytes:
                return None
            self._blobs[blob_id] = (png, 1)
            self._ids[picture_id] = (blob_id, time.monotonic() + self._ttl_seconds)
            self._note_locked(picture_id)
        return picture_id

    def alias(self, picture_id: str) -> str | None:
        """Another id for bytes already stored. Does not add to the byte budget."""
        new_id = secrets.token_urlsafe(16)
        with self._lock:
            self._sweep_locked()
            current = self._ids.get(picture_id)
            if current is None:
                return None
            blob_id, _deadline = current
            png, refs = self._blobs[blob_id]
            self._blobs[blob_id] = (png, refs + 1)
            self._ids[new_id] = (blob_id, time.monotonic() + self._ttl_seconds)
            self._note_locked(new_id)
        return new_id

    def get(self, picture_id: str) -> bytes | None:
        with self._lock:
            self._sweep_locked()
            return self._pop_locked(picture_id)

    def release(self, picture_id: str) -> None:
        with self._lock:
            self._pop_locked(picture_id)

    def release_many(self, picture_ids: list[str]) -> None:
        with self._lock:
            for picture_id in picture_ids:
                self._pop_locked(picture_id)

    @contextmanager
    def tracking(self) -> Iterator[None]:
        """Drop every id stored inside the block when the block raises."""
        bucket: list[str] = []
        with self._lock:
            self._trackers.append(bucket)
        try:
            yield
        except BaseException:
            self.release_many(list(bucket))
            raise
        finally:
            with self._lock:
                if bucket in self._trackers:
                    self._trackers.remove(bucket)

    def _note_locked(self, picture_id: str) -> None:
        for bucket in self._trackers:
            bucket.append(picture_id)

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._sweep_locked()
            return {"bytes": self._byte_count_locked(), "ids": len(self._ids)}

    def _pop_locked(self, picture_id: str) -> bytes | None:
        item = self._ids.pop(picture_id, None)
        if item is None:
            return None
        blob_id, _deadline = item
        png, refs = self._blobs[blob_id]
        if refs <= 1:
            del self._blobs[blob_id]
        else:
            self._blobs[blob_id] = (png, refs - 1)
        return png

    def _byte_count_locked(self) -> int:
        return sum(len(png) for png, _refs in self._blobs.values())

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, (_blob, deadline) in self._ids.items() if deadline <= now]
        for key in expired:
            self._pop_locked(key)


def empty_picture_report(*, requested: bool) -> dict[str, Any]:
    return {
        "scope": "embedded_raster",
        "requested": requested,
        "truncated": False,
        "listed": 0,
        "skipped": 0,
        "reasons": {key: 0 for key in REASON_KEYS},
    }


def cut_embedded_pictures(document: pymupdf.Document) -> dict[str, Any]:
    """Return listed pictures and why other embedded rasters were left out.

    ``scope`` is ``embedded_raster``. The counts do not include vector drawings
    or figures inferred from page layout.
    """
    max_images = _positive_int("MAX_PICTURES", 80)
    max_stream_bytes = _positive_int("MAX_PICTURE_STREAM_BYTES", 8 * 1024 * 1024)
    max_png_bytes = _positive_int("MAX_PICTURE_PNG_BYTES", 64 * 1024 * 1024)
    deadline_seconds = _positive_float("PICTURE_EXTRACT_TIMEOUT_SECONDS", 20.0)
    started = time.monotonic()
    png_total = 0
    pictures: list[dict[str, Any]] = []
    stored_ids: list[str] = []
    reasons = {key: 0 for key in REASON_KEYS}
    skipped = 0
    stored_xref: dict[int, str] = {}
    xref_size: dict[int, tuple[int, int]] = {}
    stop_reason: str | None = None

    try:
        for page_index, page in enumerate(document):
            images = page.get_images(full=True)
            smasks = {item[1] for item in images if item[1]}
            index_on_page = 0
            for item in images:
                xref = int(item[0])
                if xref in smasks:
                    continue
                width = int(item[2])
                height = int(item[3])
                if width < _MIN_SIDE or height < _MIN_SIDE:
                    continue
                if stop_reason is not None:
                    reasons[stop_reason] += 1
                    skipped += 1
                    continue
                if time.monotonic() - started >= deadline_seconds:
                    stop_reason = "time"
                    reasons["time"] += 1
                    skipped += 1
                    continue
                if len(pictures) >= max_images:
                    stop_reason = "count"
                    reasons["count"] += 1
                    skipped += 1
                    continue
                cached_id = stored_xref.get(xref)
                if cached_id is not None:
                    picture_id = picture_store.alias(cached_id)
                    if picture_id is None:
                        reasons["storage"] += 1
                        skipped += 1
                        continue
                    known_width, known_height = xref_size[xref]
                    pictures.append(
                        {
                            "id": picture_id,
                            "page": page_index + 1,
                            "index_on_page": index_on_page,
                            "width": known_width,
                            "height": known_height,
                        }
                    )
                    stored_ids.append(picture_id)
                    index_on_page += 1
                    continue
                if width * height * 4 > max_png_bytes - png_total:
                    reasons["decoded_size"] += 1
                    skipped += 1
                    continue
                stream_length = _stream_length(document, xref)
                if stream_length is None or stream_length > max_stream_bytes:
                    reasons["encoded_size"] += 1
                    skipped += 1
                    continue
                png = _png_bytes(document, xref)
                if png is None:
                    reasons["unreadable"] += 1
                    skipped += 1
                    continue
                if png_total + len(png) > max_png_bytes:
                    reasons["decoded_size"] += 1
                    skipped += 1
                    continue
                picture_id = picture_store.put(png)
                if picture_id is None:
                    reasons["storage"] += 1
                    skipped += 1
                    continue
                pictures.append(
                    {
                        "id": picture_id,
                        "page": page_index + 1,
                        "index_on_page": index_on_page,
                        "width": width,
                        "height": height,
                    }
                )
                stored_ids.append(picture_id)
                stored_xref[xref] = picture_id
                xref_size[xref] = (width, height)
                png_total += len(png)
                index_on_page += 1
    except Exception:
        picture_store.release_many(stored_ids)
        raise

    report = empty_picture_report(requested=True)
    report["truncated"] = skipped > 0
    report["listed"] = len(pictures)
    report["skipped"] = skipped
    report["reasons"] = reasons
    return {"pictures": pictures, "report": report}


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
