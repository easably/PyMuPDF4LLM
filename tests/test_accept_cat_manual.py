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


def _png(width: int, height: int) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 0)
    pixmap.set_rect(pixmap.irect, (10, 20, 30))
    return pixmap.tobytes("png")
