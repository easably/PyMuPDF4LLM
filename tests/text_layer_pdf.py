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
