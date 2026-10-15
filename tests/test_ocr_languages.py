"""Language codes follow the dictionaries on disk, for every installed language."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pymupdf4llm_service.pdf_to_page_markdown import (
    UnsupportedOcrLanguage,
    pdf_to_page_markdown,
)
from tests.text_layer_pdf import text_layer_pdf


def test_language_files_match_the_pinned_manifest() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "fetch_tessdata.py"
    completed = subprocess.run([sys.executable, str(script), "--check"], check=False)
    assert completed.returncode == 0


@pytest.mark.parametrize("language", ["deu", "chi_sim", "eng+rus", "script/Latin"])
def test_installed_language_does_not_ocr_a_text_page(language, monkeypatch) -> None:
    def spy(*_args, **_kwargs):
        raise AssertionError("a text page must not be OCR'd")

    monkeypatch.setattr("pymupdf4llm_service.pdf_to_page_markdown.ocr_page", spy)
    result = pdf_to_page_markdown(
        text_layer_pdf("Maintenance interval 10 hours"),
        ocr_language=language,
    )
    assert "10 hours" in result["pages"][0]["markdown"]


@pytest.mark.parametrize("language", ["english", "eng/rus", "+eng", "script/../Latin"])
def test_malformed_language_is_rejected(language) -> None:
    with pytest.raises(UnsupportedOcrLanguage):
        pdf_to_page_markdown(text_layer_pdf("Service points"), ocr_language=language)


def test_missing_language_file_is_rejected() -> None:
    with pytest.raises(UnsupportedOcrLanguage):
        pdf_to_page_markdown(text_layer_pdf("Service points"), ocr_language="nosuch")
