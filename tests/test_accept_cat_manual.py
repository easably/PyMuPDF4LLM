"""The CAT manual acceptance script stays off CI's PDF and off the live service name."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pymupdf

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "accept_cat_manual.py"
_SPEC = importlib.util.spec_from_file_location("accept_cat_manual", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
accept = importlib.util.module_from_spec(_SPEC)
sys.modules["accept_cat_manual"] = accept
_SPEC.loader.exec_module(accept)


def test_missing_pdf_message_names_the_path_variable() -> None:
    message = accept.missing_pdf_message(None)
    assert "CAT_MANUAL_PDF" in message
    assert "--pdf" in message
    assert accept.EXPECTED_SHA256 in message
    assert "pytest" in message


def test_deploy_mode_refuses_the_live_service_name() -> None:
    refusal = accept.refuse_shared_service("http://pymupdf4llm:8080", allow=False)
    assert refusal is not None
    assert "pymupdf4llm" in refusal
    assert accept.refuse_shared_service("http://pymupdf4llm:8080", allow=True) is None
    assert accept.refuse_shared_service("http://pymupdf4llm-accept:8080", allow=False) is None
    assert accept.refuse_shared_service("http://127.0.0.1:8080", allow=False) is None


def test_inventory_separates_masks_small_images_and_repeats() -> None:
    png = _png(120, 140)
    small = _png(8, 8)
    document = pymupdf.open()
    first = document.new_page()
    first.insert_image(pymupdf.Rect(0, 0, 120, 140), stream=png)
    xref = int(first.get_images()[0][0])
    first.insert_image(pymupdf.Rect(200, 0, 208, 8), stream=small)
    second = document.new_page()
    second.insert_image(pymupdf.Rect(0, 0, 120, 140), xref=xref)
    inventory = accept.inventory_document(document)
    document.close()

    counts = inventory.counts()
    assert inventory.page_count == 2
    assert counts["occurrences"] == 3
    assert counts["unique_xrefs"] == 2
    assert counts["xrefs_repeated"] == 1
    assert counts["min_side"] == 1
    assert counts["eligible"] == 2
    assert counts["mask"] == 0


def test_memory_log_peak_and_png_header() -> None:
    assert accept.peak_memory_line("100MiB / 4GiB\n1.5GiB / 4GiB\n") == "1.5GiB"
    png = _png(12, 9)
    header = accept.png_ihdr(png)
    assert header is not None
    assert header[0] == 12
    assert header[1] == 9


def test_spot_check_compares_only_embedded_rasters(monkeypatch) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 120, 140), stream=_png(120, 140))
    xref = int(page.get_images()[0][0])
    raster = accept._extract_png(document, xref)
    assert raster is not None
    vector = _png(200, 100)
    pictures = [
        {
            "id": "vector",
            "page": 1,
            "index_on_page": 0,
            "width": 200,
            "height": 100,
            "kind": "vector_drawing",
        },
        {
            "id": "raster",
            "page": 1,
            "index_on_page": 1,
            "width": 120,
            "height": 140,
            "kind": "embedded_raster",
        },
    ]
    downloads = [
        accept.Download("vector", 1, 0, 200, 100, 200, 0.01, vector, ""),
        accept.Download("raster", 1, 1, 120, 140, 200, 0.01, raster, ""),
    ]
    monkeypatch.setattr(accept, "FOCUS_PAGES", (1,))
    inventory = accept.inventory_document(document)

    spots = accept.spot_check(document, inventory, pictures, downloads, None)
    document.close()

    assert spots[0].api_count == 1
    assert spots[0].errors == []
    assert "1 vector drawing(s)" in spots[0].notes[0]


def test_spot_check_matches_same_sized_rasters_independent_of_api_order(
    monkeypatch,
) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(
        pymupdf.Rect(0, 200, 120, 340),
        stream=_png(120, 140, red=10),
    )
    page.insert_image(
        pymupdf.Rect(0, 0, 120, 140),
        stream=_png(120, 140, red=200),
    )
    xrefs = [int(item[0]) for item in page.get_images()]
    pngs = [accept._extract_png(document, xref) for xref in reversed(xrefs)]
    assert all(png is not None for png in pngs)
    pictures = [
        {
            "id": f"raster-{index}",
            "page": 1,
            "index_on_page": index,
            "width": 120,
            "height": 140,
            "kind": "embedded_raster",
        }
        for index in range(2)
    ]
    downloads = [
        accept.Download(
            f"raster-{index}",
            1,
            index,
            120,
            140,
            200,
            0.01,
            png or b"",
            "",
        )
        for index, png in enumerate(pngs)
    ]
    monkeypatch.setattr(accept, "FOCUS_PAGES", (1,))

    spots = accept.spot_check(
        document,
        accept.inventory_document(document),
        pictures,
        downloads,
        None,
    )
    document.close()

    assert spots[0].errors == []


def _png(width: int, height: int, red: int = 10) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 0)
    pixmap.set_rect(pixmap.irect, (red, 20, 30))
    return pixmap.tobytes("png")
