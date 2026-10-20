"""Cut embedded rasters and vector drawings while the PDF is open for text.

The convert JSON lists each occurrence by its own id. The bytes stay here
until that id is fetched. A picture is skipped before pixel decode when its
compressed stream or pixel size is over the budget, or when that size cannot
be measured.

Vector drawings use refined layout regions plus a drawing-cluster fallback.
Rendered scan pages are outside this list. ``PICTURE_EXTRACT_TIMEOUT_SECONDS``
(default 20) is checked between pictures. It does not interrupt work that has
already started.

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
_MIN_DISPLAY_SIDE_PT = 40.0
REASON_KEYS = (
    "time",
    "count",
    "encoded_size",
    "decoded_size",
    "unreadable",
    "storage",
    "render_failure",
    "too_small_displayed",
)


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
        "scope": "embedded_raster_and_vector_drawing",
        "requested": requested,
        "truncated": False,
        "listed": 0,
        "skipped": 0,
        "reasons": {key: 0 for key in REASON_KEYS},
        "by_kind": {
            "embedded_raster": {"listed": 0, "skipped": 0},
            "vector_drawing": {"listed": 0, "skipped": 0},
        },
        "vector_detectors": {"layout": 0, "fallback": 0},
        "vector_id_actions": {"split": 0, "merged": 0},
        "vector_pages_scanned": 0,
        "vector_pages_total": 0,
        "vector_slowest_pages": [],
        "raster_cap_reached_at_page": None,
    }


def cut_embedded_pictures(
    document: pymupdf.Document,
    *,
    started: float | None = None,
    keep_internal: bool = False,
) -> dict[str, Any]:
    """Return listed pictures and why other embedded rasters were left out.

    ``scope`` is ``embedded_raster``. The counts do not include vector drawings
    or figures inferred from page layout.
    """
    max_images = _positive_int(
        "MAX_RASTER_PICTURES", _positive_int("MAX_PICTURES", 80)
    )
    total_cap = _optional_positive_int("MAX_PICTURES")
    if total_cap is not None:
        max_images = min(max_images, total_cap)
    max_stream_bytes = _positive_int("MAX_PICTURE_STREAM_BYTES", 8 * 1024 * 1024)
    max_png_bytes = _positive_int("MAX_PICTURE_PNG_BYTES", 64 * 1024 * 1024)
    deadline_seconds = _positive_float("PICTURE_EXTRACT_TIMEOUT_SECONDS", 20.0)
    min_display_side = _positive_float(
        "MIN_PICTURE_DISPLAY_SIDE_PT", _MIN_DISPLAY_SIDE_PT
    )
    started = started if started is not None else time.monotonic()
    png_total = 0
    pictures: list[dict[str, Any]] = []
    stored_ids: list[str] = []
    reasons = {key: 0 for key in REASON_KEYS}
    skipped = 0
    stored_xref: dict[int, str] = {}
    xref_size: dict[int, tuple[int, int]] = {}
    stop_reason: str | None = None
    raster_cap_page: int | None = None

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
                if _displayed_too_small(page, xref, min_display_side):
                    reasons["too_small_displayed"] += 1
                    skipped += 1
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
                    if raster_cap_page is None:
                        raster_cap_page = page_index + 1
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
                            "kind": "embedded_raster",
                            "_bbox": _image_box(page, xref),
                        }
                    )
                    stored_ids.append(picture_id)
                    index_on_page += 1
                    continue
                if width * height * 4 > max_png_bytes - png_total:
                    reasons["decoded_size"] += 1
                    skipped += 1
                    continue
                stream_length = _stream_length(
                    document,
                    xref,
                    allow_raw_measure=True,
                )
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
                        "kind": "embedded_raster",
                        "_bbox": _image_box(page, xref),
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
    report["by_kind"]["embedded_raster"] = {
        "listed": len(pictures),
        "skipped": skipped,
    }
    report["raster_cap_reached_at_page"] = raster_cap_page
    report["_png_bytes"] = png_total
    _sort_and_index(pictures)
    if not keep_internal:
        _strip_internal(pictures, report)
    return {"pictures": pictures, "report": report}


def cut_pictures(
    document: pymupdf.Document,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cut embedded rasters and detected vector drawings under shared budgets."""
    from pymupdf4llm_service.vector_drawings import (
        VectorDrawingSettings,
        detect_vector_drawings,
        useful_vector_paths,
    )

    started = time.monotonic()
    cut = cut_embedded_pictures(
        document,
        started=started,
        keep_internal=True,
    )
    pictures = cut["pictures"]
    report = cut["report"]
    reasons = report["reasons"]
    max_png_bytes = _positive_int("MAX_PICTURE_PNG_BYTES", 64 * 1024 * 1024)
    png_total = int(report.get("_png_bytes") or 0)
    vector_started = time.monotonic()
    deadline_seconds = _positive_float("VECTOR_DETECTION_TIMEOUT_SECONDS", 60.0)
    max_vectors = _positive_int("MAX_VECTOR_PICTURES", 120)
    total_cap = _optional_positive_int("MAX_PICTURES")
    settings = VectorDrawingSettings.from_environment()
    vector_listed = 0
    vector_skipped = 0
    detectors = {"layout": 0, "fallback": 0}
    id_actions = {"split": 0, "merged": 0}
    pages_scanned = 0
    page_timings: list[tuple[float, int]] = []
    chunks_by_page = {
        int((chunk.get("metadata") or {}).get("page_number") or 0): chunk
        for chunk in chunks
    }
    listed_rasters_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for picture in pictures:
        listed_rasters_by_page.setdefault(int(picture["page"]), []).append(
            tuple(picture["_bbox"])
        )

    try:
        for page_number in range(1, document.page_count + 1):
            if time.monotonic() - vector_started >= deadline_seconds:
                reasons["time"] += 1
                vector_skipped += 1
                break
            page_started = time.monotonic()
            page = document[page_number - 1]
            chunk = chunks_by_page.get(page_number) or {}
            layout_boxes = [
                box["bbox"]
                for box in chunk.get("page_boxes") or []
                if box.get("class") == "picture"
            ]
            paths = page.get_drawings()
            useful_paths = useful_vector_paths(page, paths)
            if layout_boxes or len(useful_paths) >= settings.prefilter_min_paths:
                candidates = detect_vector_drawings(
                    page,
                    layout_boxes,
                    settings,
                    raster_boxes=listed_rasters_by_page.get(page_number, []),
                    paths=paths,
                )
            else:
                candidates = []
            pages_scanned += 1
            page_timings.append((time.monotonic() - page_started, page_number))
            for candidate in candidates:
                stop_reason = None
                if time.monotonic() - vector_started >= deadline_seconds:
                    stop_reason = "time"
                elif vector_listed >= max_vectors:
                    stop_reason = "count"
                elif total_cap is not None and len(pictures) >= total_cap:
                    stop_reason = "count"
                if stop_reason is not None:
                    reasons[stop_reason] += 1
                    vector_skipped += 1
                    continue
                box = candidate["bbox"]
                try:
                    pixmap = page.get_pixmap(
                        clip=pymupdf.Rect(box),
                        dpi=150,
                        alpha=False,
                    )
                    png = pixmap.tobytes("png")
                except Exception:
                    reasons["render_failure"] += 1
                    vector_skipped += 1
                    continue
                if png_total + len(png) > max_png_bytes:
                    reasons["decoded_size"] += 1
                    vector_skipped += 1
                    continue
                picture_id = picture_store.put(png)
                if picture_id is None:
                    reasons["storage"] += 1
                    vector_skipped += 1
                    continue
                pictures.append(
                    {
                        "id": picture_id,
                        "page": page_number,
                        "index_on_page": 0,
                        "width": pixmap.width,
                        "height": pixmap.height,
                        "kind": "vector_drawing",
                        "_bbox": tuple(box),
                    }
                )
                vector_listed += 1
                png_total += len(png)
                detectors[candidate["detector"]] += 1
                id_actions["split"] += int(candidate.get("id_split", False))
                id_actions["merged"] += int(candidate.get("id_merged", False))
    except Exception:
        picture_store.release_many([item["id"] for item in pictures])
        raise

    report["listed"] = len(pictures)
    report["skipped"] += vector_skipped
    report["truncated"] = (
        report["skipped"] > 0 or pages_scanned < document.page_count
    )
    report["by_kind"]["vector_drawing"] = {
        "listed": vector_listed,
        "skipped": vector_skipped,
    }
    report["vector_detectors"] = detectors
    report["vector_id_actions"] = id_actions
    report["vector_pages_scanned"] = pages_scanned
    report["vector_pages_total"] = document.page_count
    report["vector_slowest_pages"] = [
        {"page": page, "seconds": round(seconds, 6)}
        for seconds, page in sorted(page_timings, reverse=True)[:5]
    ]
    report["_png_bytes"] = png_total
    _sort_and_index(pictures)
    _strip_internal(pictures, report)
    return cut


def _displayed_too_small(
    page: pymupdf.Page,
    xref: int,
    min_side_pt: float,
) -> bool:
    """True when every shown box of this image is smaller than the point floor."""
    rects = page.get_image_rects(xref)
    if not rects:
        return False
    return all(min(rect.width, rect.height) < min_side_pt for rect in rects)


def _image_box(page: pymupdf.Page, xref: int) -> tuple[float, float, float, float]:
    rects = page.get_image_rects(xref)
    if not rects:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(map(float, rects[0]))


def _sort_and_index(pictures: list[dict[str, Any]]) -> None:
    pictures.sort(
        key=lambda item: (
            int(item["page"]),
            float(item.get("_bbox", (0, 0, 0, 0))[1]),
            float(item.get("_bbox", (0, 0, 0, 0))[0]),
            item["kind"],
        )
    )
    current_page = None
    index = 0
    for item in pictures:
        if item["page"] != current_page:
            current_page = item["page"]
            index = 0
        item["index_on_page"] = index
        index += 1


def _strip_internal(
    pictures: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    for item in pictures:
        item.pop("_bbox", None)
    report.pop("_png_bytes", None)


def _stream_length(
    document: pymupdf.Document,
    xref: int,
    *,
    allow_raw_measure: bool = False,
) -> int | None:
    kind, value = document.xref_get_key(xref, "Length")
    if kind == "int":
        try:
            return int(value)
        except ValueError:
            pass
    elif kind == "xref":
        try:
            length_xref = int(value.split()[0])
            return int(document.xref_object(length_xref).strip())
        except (ValueError, IndexError):
            pass
    if allow_raw_measure:
        try:
            return len(document.xref_stream_raw(xref))
        except Exception:
            return None
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


def _optional_positive_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


picture_store = PictureStore()
