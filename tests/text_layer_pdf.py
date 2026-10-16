"""Build a small PDF whose pages already contain selectable text."""

from __future__ import annotations

import pymupdf


def text_layer_pdf(*page_texts: str) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def mixed_pdf(text_page: str, scan_text: str, *, cjk_font: bool = False) -> bytes:
    """One selectable page, then one page that is only a picture of text."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text_page, fontsize=16)
    scan = document.new_page()
    scan.insert_image(scan.rect, pixmap=_text_pixmap(scan_text, cjk_font=cjk_font))
    data = document.tobytes()
    document.close()
    return data


def text_and_scan_same_page(text: str, scan_text: str) -> bytes:
    """A text layer plus a large image that PyMuPDF4LLM asks to OCR."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image((0, 100, page.rect.width, page.rect.height), pixmap=_text_pixmap(scan_text))
    page.insert_text((72, 72), text, fontsize=16)
    data = document.tobytes()
    document.close()
    return data


def _text_pixmap(text: str, *, cjk_font: bool = False) -> pymupdf.Pixmap:
    source = pymupdf.open()
    page = source.new_page()
    if cjk_font:
        font = pymupdf.Font("cjk")
        page.insert_font(fontname="cjk", fontbuffer=font.buffer)
        page.insert_text((72, 200), text, fontsize=36, fontname="cjk")
    else:
        page.insert_text((72, 200), text, fontsize=28)
    pixmap = page.get_pixmap(dpi=150, colorspace=pymupdf.csRGB)
    source.close()
    return pixmap
