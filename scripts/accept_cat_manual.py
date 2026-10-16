#!/usr/bin/env python3
"""Manual acceptance run for the CAT operator manual.

This is not part of pytest or CI. The PDF stays on disk. Pass its path with
--pdf or CAT_MANUAL_PDF. A missing file prints where to point the path.

Two modes:

- local: BASE_URL is a service you started yourself, usually http://127.0.0.1:8080
- deploy: BASE_URL is an acceptance container started from the deployed image.
  The script refuses http://pymupdf4llm:8080 unless --allow-shared-service is
  set, because that name is the live Doc2Graph target and this PDF would take
  its only conversion slot.

The run checks SHA-256 and page count, inventories embedded images locally,
then POSTs the file over HTTP. It does not set a pass/fail time limit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

EXPECTED_SHA256 = "5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab"
EXPECTED_PAGES = 530
FOCUS_PAGES = (24, 26, 27, 419, 420)
LIVE_SERVICE_HOSTS = {"pymupdf4llm"}

# Same defaults as pymupdf4llm_service.pictures. Inventory uses these static
# checks. The running service may stop earlier on time, count, or memory.
MIN_SIDE = 100
MAX_PICTURES = 80
MAX_STREAM_BYTES = 8 * 1024 * 1024
MAX_PNG_BYTES = 64 * 1024 * 1024
PICTURE_TIMEOUT_SECONDS = 20.0
STORE_BYTES = 64 * 1024 * 1024

_MEM_UNITS = {
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "kB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "B": 1,
}


@dataclass
class Occurrence:
    page: int
    xref: int
    width: int
    height: int
    stream_bytes: int | None
    # None means the static filters would let the service decode this image.
    # mask and min_side are ignored by the service and are not API omissions.
    static_filter: str | None


@dataclass
class Inventory:
    page_count: int
    occurrences: list[Occurrence]

    def counts(self) -> dict[str, int]:
        filters = {"mask": 0, "min_side": 0, "encoded_size": 0, "decoded_size": 0, "eligible": 0}
        pages_with_images: set[int] = set()
        for item in self.occurrences:
            pages_with_images.add(item.page)
            if item.static_filter is None:
                filters["eligible"] += 1
            else:
                filters[item.static_filter] += 1
        xrefs = [item.xref for item in self.occurrences]
        unique = set(xrefs)
        repeats = 0
        for xref in unique:
            if xrefs.count(xref) > 1:
                repeats += 1
        return {
            "occurrences": len(self.occurrences),
            "pages_with_images": len(pages_with_images),
            "unique_xrefs": len(unique),
            "xrefs_repeated": repeats,
            **filters,
        }


@dataclass
class Download:
    picture_id: str
    page: int
    index_on_page: int
    width: int
    height: int
    status: int
    seconds: float
    png: bytes
    error: str


@dataclass
class Spot:
    page: int
    api_count: int
    eligible_count: int
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    pdf_path: str
    sha256: str
    page_count: int
    inventory: Inventory
    inventory_seconds: float
    mode: str
    base_url: str
    text_request_seconds: float | None = None
    text_status: int | None = None
    text_bytes: int | None = None
    text_pages: int | None = None
    full_request_seconds: float | None = None
    full_status: int | None = None
    full_bytes: int | None = None
    full_body: dict[str, Any] | None = None
    downloads: list[Download] = field(default_factory=list)
    download_seconds: float = 0.0
    spots: list[Spot] = field(default_factory=list)
    text_peak_memory: str | None = None
    full_peak_memory: str | None = None
    download_peak_memory: str | None = None
    errors: list[str] = field(default_factory=list)
    meta_host: str = ""
    meta_image: str = ""
    meta_commit: str = ""
    meta_container: str = ""


def missing_pdf_message(path: str | None) -> str:
    shown = path or "(not set)"
    return (
        f"PDF not found: {shown}\n"
        "Pass --pdf PATH or set CAT_MANUAL_PDF to the local file "
        "cat-operator-manual-320-323.pdf.\n"
        f"Expected SHA-256 {EXPECTED_SHA256} and {EXPECTED_PAGES} pages.\n"
        "The file is not in git. pytest and CI do not read it."
    )


def refuse_shared_service(base_url: str, allow: bool) -> str | None:
    host = urllib.request.urlparse(base_url).hostname or ""
    if host in LIVE_SERVICE_HOSTS and not allow:
        return (
            f"Refusing {base_url}: host {host} is the live Doc2Graph service.\n"
            "Start a second container from the same image and pass its BASE_URL.\n"
            "That keeps this PDF off the single conversion slot used by normal requests.\n"
            "Re-run with --allow-shared-service only when you mean to use that slot."
        )
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_memory_token(token: str) -> int | None:
    text = token.strip()
    for unit, scale in sorted(_MEM_UNITS.items(), key=lambda item: -len(item[0])):
        if text.endswith(unit):
            try:
                return int(float(text[: -len(unit)]) * scale)
            except ValueError:
                return None
    return None


def peak_memory_line(log_text: str) -> str | None:
    best_bytes = -1
    best_line = None
    for line in log_text.splitlines():
        piece = line.strip().split()
        if not piece:
            continue
        size = parse_memory_token(piece[0])
        if size is not None and size >= best_bytes:
            best_bytes = size
            best_line = piece[0]
    return best_line


def read_peak_since(path: Path | None, offset: int) -> str | None:
    if path is None or not path.is_file():
        return None
    data = path.read_text(encoding="utf-8", errors="replace")
    if offset > len(data):
        offset = 0
    return peak_memory_line(data[offset:])


def log_offset(path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    return path.stat().st_size


def inventory_document(document: pymupdf.Document) -> Inventory:
    occurrences: list[Occurrence] = []
    for page_index, page in enumerate(document):
        images = page.get_images(full=True)
        masks = {int(item[1]) for item in images if item[1]}
        for item in images:
            xref = int(item[0])
            width = int(item[2])
            height = int(item[3])
            static_filter: str | None = None
            stream_bytes: int | None = None
            if xref in masks:
                static_filter = "mask"
            elif width < MIN_SIDE or height < MIN_SIDE:
                static_filter = "min_side"
            else:
                stream_bytes = _stream_length(document, xref)
                if stream_bytes is None or stream_bytes > MAX_STREAM_BYTES:
                    static_filter = "encoded_size"
                elif width * height * 4 > MAX_PNG_BYTES:
                    static_filter = "decoded_size"
            occurrences.append(
                Occurrence(
                    page=page_index + 1,
                    xref=xref,
                    width=width,
                    height=height,
                    stream_bytes=stream_bytes,
                    static_filter=static_filter,
                )
            )
    return Inventory(page_count=document.page_count, occurrences=occurrences)


def _stream_length(document: pymupdf.Document, xref: int) -> int | None:
    kind, value = document.xref_get_key(xref, "Length")
    if kind != "int":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def png_ihdr(png: bytes) -> tuple[int, int, int, int] | None:
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 26:
        return None
    if png[12:16] != b"IHDR":
        return None
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width, height, png[24], png[25]


def _extract_png(document: pymupdf.Document, xref: int) -> bytes | None:
    try:
        extracted = document.extract_image(xref)
        pixmap = pymupdf.Pixmap(extracted["image"])
        return pixmap.tobytes("png")
    except Exception:
        return None


def spot_check(
    document: pymupdf.Document,
    inventory: Inventory,
    pictures: list[dict[str, Any]],
    downloads: list[Download],
    save_dir: Path | None,
) -> list[Spot]:
    by_id = {item.picture_id: item for item in downloads}
    eligible = [item for item in inventory.occurrences if item.static_filter is None]
    cursor = 0
    spots: list[Spot] = []
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
    for page in FOCUS_PAGES:
        api_pics = [item for item in pictures if int(item.get("page") or 0) == page]
        api_pics.sort(key=lambda item: int(item.get("index_on_page") or 0))
        page_eligible = [item for item in eligible if item.page == page]
        spot = Spot(page=page, api_count=len(api_pics), eligible_count=len(page_eligible))
        indexes = [int(item.get("index_on_page") or 0) for item in api_pics]
        if indexes != list(range(len(indexes))):
            spot.errors.append(f"index_on_page is not 0..{len(indexes) - 1}: {indexes}")
        seen_hash: dict[str, int] = {}
        for picture in api_pics:
            if int(picture.get("page") or 0) != page:
                spot.errors.append("picture page does not match the focus page")
                continue
            download = by_id.get(str(picture.get("id")))
            if download is None or download.status != 200:
                spot.errors.append(f"id {picture.get('id')} was not downloaded")
                continue
            ihdr = png_ihdr(download.png)
            if ihdr is None:
                spot.errors.append(f"id {picture.get('id')} is not a PNG")
                continue
            if ihdr[0] != int(picture["width"]) or ihdr[1] != int(picture["height"]):
                spot.errors.append(
                    f"PNG {ihdr[0]}x{ihdr[1]} != listed {picture['width']}x{picture['height']}"
                )
            digest = hashlib.sha256(download.png).hexdigest()
            if digest in seen_hash:
                spot.notes.append(
                    f"same PNG bytes on page {page}: index {seen_hash[digest]} and {picture.get('index_on_page')}"
                )
            else:
                seen_hash[digest] = int(picture.get("index_on_page") or 0)
            match: Occurrence | None = None
            while cursor < len(eligible):
                candidate = eligible[cursor]
                cursor += 1
                if (
                    candidate.page == page
                    and candidate.width == int(picture["width"])
                    and candidate.height == int(picture["height"])
                ):
                    match = candidate
                    break
            if match is None:
                spot.errors.append(
                    f"no eligible PDF image matches {picture['width']}x{picture['height']} on page {page}"
                )
                continue
            local = _extract_png(document, match.xref)
            if local is None:
                spot.errors.append(f"xref {match.xref} did not extract locally")
                continue
            if hashlib.sha256(local).hexdigest() != digest:
                spot.errors.append(
                    f"PNG bytes for xref {match.xref} differ from the service response"
                )
            else:
                spot.notes.append(
                    f"index {picture.get('index_on_page')} xref {match.xref} "
                    f"{match.width}x{match.height} matches the local extract"
                )
            if save_dir is not None:
                target = save_dir / f"page-{page}-index-{picture.get('index_on_page')}.png"
                target.write_bytes(download.png)
        if not api_pics:
            spot.notes.append(
                "service returned no picture on this page; "
                f"static inventory still has {len(page_eligible)} size-eligible images"
            )
        if save_dir is not None:
            rendered = document[page - 1].get_pixmap(dpi=72, alpha=False)
            rendered.save(str(save_dir / f"page-{page}-render.png"))
        spots.append(spot)
    return spots


def _multipart(pdf: bytes, include_pictures: bool) -> tuple[bytes, str]:
    boundary = "----CatManualBoundary7f3a"
    fields = [
        ("ocr_language", "eng"),
        ("include_pictures", "true" if include_pictures else "false"),
    ]
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        b'Content-Disposition: form-data; name="file"; filename="cat-operator-manual.pdf"\r\n'
    )
    chunks.append(b"Content-Type: application/pdf\r\n\r\n")
    chunks.append(pdf)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _request(url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> tuple[int, bytes, float]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    elapsed = time.perf_counter() - started
    return status, body, elapsed


def wait_until_healthy(base_url: str) -> None:
    last = "no response"
    for _ in range(60):
        try:
            status, body, _elapsed = _request(base_url.rstrip("/") + "/health", None, {}, 10)
            if status == 200 and json.loads(body).get("status") == "ok":
                return
            last = body.decode("utf-8", errors="replace")
        except Exception as exc:
            last = str(exc)
        time.sleep(1)
    raise SystemExit(f"health did not answer: {last}")


def post_convert(base_url: str, pdf: bytes, include_pictures: bool) -> tuple[int, bytes, float]:
    payload, content_type = _multipart(pdf, include_pictures)
    return _request(
        base_url.rstrip("/") + "/v1/convert",
        payload,
        {"Content-Type": content_type},
        3600,
    )


def download_pictures(base_url: str, pictures: list[dict[str, Any]]) -> tuple[list[Download], float]:
    started = time.perf_counter()
    downloads: list[Download] = []
    for item in pictures:
        picture_id = str(item.get("id") or "")
        url = base_url.rstrip("/") + "/v1/pictures/" + urllib.request.quote(picture_id, safe="")
        error = ""
        try:
            status, body, elapsed = _request(url, None, {}, 120)
        except Exception as exc:
            status, body, elapsed = 0, b"", 0.0
            error = str(exc)
        if status != 200 and not error:
            error = body.decode("utf-8", errors="replace")[:200]
        downloads.append(
            Download(
                picture_id=picture_id,
                page=int(item.get("page") or 0),
                index_on_page=int(item.get("index_on_page") or 0),
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                status=status,
                seconds=elapsed,
                png=body if status == 200 else b"",
                error=error,
            )
        )
    return downloads, time.perf_counter() - started


def repeat_examples(inventory: Inventory, limit: int = 12) -> list[str]:
    grouped: dict[int, list[int]] = {}
    for item in inventory.occurrences:
        grouped.setdefault(item.xref, []).append(item.page)
    rows = [(xref, pages) for xref, pages in grouped.items() if len(pages) > 1]
    rows.sort(key=lambda item: (-len(item[1]), item[0]))
    lines = []
    for xref, pages in rows[:limit]:
        shown = ",".join(str(page) for page in pages[:8])
        if len(pages) > 8:
            shown += ",..."
        lines.append(f"xref {xref} on {len(pages)} pages ({shown})")
    extra = len(rows) - limit
    if extra > 0:
        lines.append(f"... {extra} more repeated xrefs")
    return lines


def page_count_line(inventory: Inventory) -> str:
    totals: dict[int, int] = {}
    for item in inventory.occurrences:
        totals[item.page] = totals.get(item.page, 0) + 1
    return ", ".join(f"{page}:{totals[page]}" for page in sorted(totals))


def render_report(result: RunResult) -> str:
    counts = result.inventory.counts()
    body = result.full_body or {}
    report = body.get("picture_report") or {}
    reasons = report.get("reasons") or {}
    pictures = body.get("pictures") or []
    pages = body.get("pages") or []
    got = sum(1 for item in result.downloads if item.status == 200 and item.png.startswith(b"\x89PNG"))
    failed = len(result.downloads) - got
    lines = [
        "# CAT manual acceptance baseline",
        "",
        "Manual HTTP run of cat-operator-manual-320-323.pdf. The PDF is not in git.",
        "The size-eligible count below is an inventory of the file. It is not the number of files the service is expected to return.",
        "",
        "## Machine and image",
        "",
        f"- Host: {result.meta_host or 'not recorded'}",
        f"- Image: {result.meta_image or 'not recorded'}",
        f"- Commit: {result.meta_commit or 'not recorded'}",
        f"- Container: {result.meta_container or 'not recorded'}",
        f"- Mode: {result.mode}",
        f"- BASE_URL: {result.base_url}",
        f"- PDF SHA-256: {result.sha256}",
        f"- PDF pages: {result.page_count}",
        "",
        "## Service limits used for the static inventory",
        "",
        "These match the image defaults. The live container does not set MAX_PICTURES or the picture byte limits.",
        "",
        f"- MAX_PICTURES: {MAX_PICTURES}",
        f"- MAX_PICTURE_STREAM_BYTES: {MAX_STREAM_BYTES}",
        f"- MAX_PICTURE_PNG_BYTES: {MAX_PNG_BYTES}",
        f"- MAX_PICTURE_STORE_BYTES: {STORE_BYTES}",
        f"- PICTURE_EXTRACT_TIMEOUT_SECONDS: {PICTURE_TIMEOUT_SECONDS}",
        f"- minimum side: {MIN_SIDE}px (masks and smaller rasters are not API omissions)",
        "",
        "## Timings",
        "",
        "No pass/fail time limit. Text time is a separate POST with include_pictures=false. Picture time is not a timer inside that process: this image does not return one. The full POST includes text again, then the picture cut.",
        "",
        f"- Inventory (local, not HTTP): {result.inventory_seconds:.3f}s",
        f"- Text request: {_seconds(result.text_request_seconds)}",
        f"- Full request (text + pictures): {_seconds(result.full_request_seconds)}",
        f"- Picture download: {result.download_seconds:.3f}s",
        f"- HTTP total (text request + full request + download): {_http_total(result):.3f}s",
        f"- Text response bytes: {result.text_bytes}",
        f"- Full response bytes: {result.full_bytes}",
        f"- Peak container memory during text request: {result.text_peak_memory or 'not recorded'}",
        f"- Peak container memory during full request: {result.full_peak_memory or 'not recorded'}",
        f"- Peak container memory during download: {result.download_peak_memory or 'not recorded'}",
        "",
        "## Text",
        "",
        f"- Non-empty text pages in the full response: {len(pages)}",
        f"- page_count: {body.get('page_count')}",
        f"- Text-only non-empty pages: {result.text_pages}",
        "",
        "## Static image inventory",
        "",
        "Counted from the PDF with PyMuPDF, not from the API. Eligible means: not a soft mask, both sides at least 100px, stream Length known and at most 8 MiB, and width*height*4 within 64 MiB. That eligible total is not an expected download count.",
        "",
        f"- Occurrences: {counts['occurrences']}",
        f"- Pages with at least one occurrence: {counts['pages_with_images']}",
        f"- Unique xrefs: {counts['unique_xrefs']}",
        f"- Xrefs that occur more than once: {counts['xrefs_repeated']}",
        f"- Rejected as mask: {counts['mask']}",
        f"- Rejected as under {MIN_SIDE}px: {counts['min_side']}",
        f"- Rejected for encoded size or missing Length: {counts['encoded_size']}",
        f"- Rejected for decoded pixel budget: {counts['decoded_size']}",
        f"- Eligible before the runtime stop: {counts['eligible']}",
        "",
        "### Repeated xrefs",
        "",
    ]
    lines.extend(f"- {line}" for line in repeat_examples(result.inventory))
    lines.extend(
        [
            "",
            "### Occurrences per page",
            "",
            page_count_line(result.inventory) or "(none)",
            "",
            "## What the API returned",
            "",
            f"- HTTP status: {result.full_status}",
            f"- pictures listed: {len(pictures)}",
            f"- picture_report: `{json.dumps(report, sort_keys=True)}`",
            f"- GET success: {got}",
            f"- GET failed: {failed}",
            "",
            "After MAX_PICTURES or the picture timeout, later images that are not masks and not under 100px are counted under that stop reason, including ones that a static size filter would also have rejected. Masks and sub-100px rasters stay out of both `listed` and `skipped`.",
            "",
        ]
    )
    if pictures:
        lines.append("| page | index_on_page | width | height | bytes | GET |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item, download in zip(pictures, result.downloads):
            lines.append(
                f"| {item.get('page')} | {item.get('index_on_page')} | {item.get('width')} | "
                f"{item.get('height')} | {len(download.png)} | {download.status} |"
            )
        lines.append("")
    stop_bits = []
    for key in ("time", "count", "storage"):
        if int(reasons.get(key) or 0) > 0:
            stop_bits.append(f"{key}={reasons.get(key)}")
    lines.extend(
        [
            "### Why the API list is shorter than the inventory",
            "",
            f"- Static eligible occurrences: {counts['eligible']}",
            f"- API listed: {report.get('listed')}",
            f"- API skipped: {report.get('skipped')}",
            f"- API truncated: {report.get('truncated')}",
            f"- Stop counters in the response: {', '.join(stop_bits) or 'none recorded'}",
            f"- Other API reasons: encoded_size={reasons.get('encoded_size', 0)}, "
            f"decoded_size={reasons.get('decoded_size', 0)}, "
            f"unreadable={reasons.get('unreadable', 0)}, storage={reasons.get('storage', 0)}",
            "",
            "The service lists at most 80 pictures and stops the cut about 20 seconds after it starts, between pictures. Memory pressure shows up as `storage` when the PNG store cannot keep another blob. None of those runtime stops is the eligible inventory count.",
            "",
            "## Focus pages",
            "",
            "Pages 24, 26, 27, 419, and 420. A page with no API row was past the runtime stop. That is recorded, not treated as a missing file.",
            "",
        ]
    )
    for spot in result.spots:
        lines.append(
            f"### Page {spot.page} — API {spot.api_count}, size-eligible in the file {spot.eligible_count}"
        )
        lines.append("")
        for note in spot.notes:
            lines.append(f"- {note}")
        for error in spot.errors:
            lines.append(f"- ERROR: {error}")
        if not spot.notes and not spot.errors:
            lines.append("- no extra notes")
        lines.append("")
    if result.errors:
        lines.extend(["## Run errors", ""])
        lines.extend(f"- {error}" for error in result.errors)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _seconds(value: float | None) -> str:
    if value is None:
        return "not run"
    return f"{value:.3f}s"


def _http_total(result: RunResult) -> float:
    return (
        (result.text_request_seconds or 0.0)
        + (result.full_request_seconds or 0.0)
        + result.download_seconds
    )


def print_log(result: RunResult) -> None:
    counts = result.inventory.counts()
    body = result.full_body or {}
    pictures = body.get("pictures") or []
    pages = body.get("pages") or []
    got = sum(1 for item in result.downloads if item.status == 200 and item.png.startswith(b"\x89PNG"))
    failed = len(result.downloads) - got
    print(f"sha256 {result.sha256}")
    print(f"pages {result.page_count}")
    print(
        "inventory "
        f"occurrences={counts['occurrences']} unique_xref={counts['unique_xrefs']} "
        f"eligible={counts['eligible']} mask={counts['mask']} min_side={counts['min_side']} "
        f"encoded_size={counts['encoded_size']} decoded_size={counts['decoded_size']} "
        f"in {result.inventory_seconds:.3f}s"
    )
    print(
        "http "
        f"text={_seconds(result.text_request_seconds)} "
        f"full={_seconds(result.full_request_seconds)} "
        f"download={result.download_seconds:.3f}s "
        f"total={_http_total(result):.3f}s"
    )
    print(
        "response "
        f"full_bytes={result.full_bytes} text_pages={len(pages)} "
        f"pictures={len(pictures)} get_ok={got} get_failed={failed}"
    )
    print(
        "memory "
        f"text={result.text_peak_memory or '-'} "
        f"full={result.full_peak_memory or '-'} "
        f"download={result.download_peak_memory or '-'}"
    )
    report = body.get("picture_report") or {}
    print(f"picture_report {json.dumps(report, sort_keys=True)}")
    for spot in result.spots:
        print(
            f"focus page {spot.page} api={spot.api_count} eligible={spot.eligible_count} "
            f"errors={len(spot.errors)}"
        )
    for error in result.errors:
        print(f"ERROR {error}", file=sys.stderr)


def run(args: argparse.Namespace) -> RunResult:
    path_text = args.pdf or os.environ.get("CAT_MANUAL_PDF")
    path = Path(path_text).expanduser() if path_text else None
    if path is None or not path.is_file():
        raise SystemExit(missing_pdf_message(path_text))
    refusal = refuse_shared_service(args.base_url, args.allow_shared_service)
    if refusal and not args.inventory_only:
        raise SystemExit(refusal)

    sha = file_sha256(path)
    if sha != EXPECTED_SHA256:
        raise SystemExit(f"SHA-256 mismatch for {path}: {sha}\nexpected {EXPECTED_SHA256}")
    pdf_bytes = path.read_bytes()
    started = time.perf_counter()
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.page_count != EXPECTED_PAGES:
            raise SystemExit(f"page count is {document.page_count}, expected {EXPECTED_PAGES}")
        inventory = inventory_document(document)
        inventory_seconds = time.perf_counter() - started
        result = RunResult(
            pdf_path=str(path),
            sha256=sha,
            page_count=document.page_count,
            inventory=inventory,
            inventory_seconds=inventory_seconds,
            mode=args.mode,
            base_url=args.base_url,
            meta_host=args.meta_host,
            meta_image=args.meta_image,
            meta_commit=args.meta_commit,
            meta_container=args.meta_container,
        )
        if args.inventory_only:
            return result

        memory_log = Path(args.memory_log) if args.memory_log else None
        wait_until_healthy(args.base_url)
        if not args.skip_text_only:
            print("POST text-only", flush=True)
            offset = log_offset(memory_log)
            status, body, elapsed = post_convert(args.base_url, pdf_bytes, False)
            result.text_request_seconds = elapsed
            result.text_status = status
            result.text_bytes = len(body)
            result.text_peak_memory = read_peak_since(memory_log, offset)
            if status != 200:
                result.errors.append(f"text POST failed: {status} {body[:300]!r}")
            else:
                parsed = json.loads(body)
                result.text_pages = len(parsed.get("pages") or [])
        print("POST include_pictures", flush=True)
        offset = log_offset(memory_log)
        status, body, elapsed = post_convert(args.base_url, pdf_bytes, True)
        result.full_request_seconds = elapsed
        result.full_status = status
        result.full_bytes = len(body)
        result.full_peak_memory = read_peak_since(memory_log, offset)
        if status != 200:
            result.errors.append(f"full POST failed: {status} {body[:300]!r}")
            return result
        parsed = json.loads(body)
        result.full_body = parsed
        pictures = parsed.get("pictures") or []
        if not isinstance(pictures, list):
            result.errors.append("pictures is not a list")
            pictures = []
        print(f"GET {len(pictures)} pictures", flush=True)
        offset = log_offset(memory_log)
        downloads, download_seconds = download_pictures(args.base_url, pictures)
        result.downloads = downloads
        result.download_seconds = download_seconds
        result.download_peak_memory = read_peak_since(memory_log, offset)
        for item in downloads:
            if item.status != 200 or not item.png.startswith(b"\x89PNG"):
                result.errors.append(
                    f"GET {item.picture_id} failed status={item.status} {item.error}"
                )
        save_dir = Path(args.save_dir) if args.save_dir else None
        result.spots = spot_check(document, inventory, pictures, downloads, save_dir)
        for spot in result.spots:
            result.errors.extend(f"page {spot.page}: {error}" for error in spot.errors)
        return result
    finally:
        document.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acceptance HTTP run for the CAT operator manual")
    parser.add_argument("--pdf", default=os.environ.get("CAT_MANUAL_PDF"))
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--mode", choices=("local", "deploy"), default=os.environ.get("ACCEPT_MODE", "local"))
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--skip-text-only", action="store_true")
    parser.add_argument("--allow-shared-service", action="store_true")
    parser.add_argument("--memory-log")
    parser.add_argument("--report")
    parser.add_argument("--save-dir")
    parser.add_argument("--meta-host", default="")
    parser.add_argument("--meta-image", default="")
    parser.add_argument("--meta-commit", default="")
    parser.add_argument("--meta-container", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run(args)
    print_log(result)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(result), encoding="utf-8")
        print(f"report {report_path}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
