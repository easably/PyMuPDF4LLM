"""Language codes follow the dictionaries on disk, for every installed language."""

from __future__ import annotations

import pytest

from pymupdf4llm_service.pdf_to_page_markdown import (
    UnsupportedOcrLanguage,
    installed_ocr_languages,
    pdf_to_page_markdown,
)
from tests.text_layer_pdf import text_layer_pdf

_BROAD_SET = {
    "eng",
    "rus",
    "deu",
    "fra",
    "spa",
    "pol",
    "ukr",
    "chi_sim",
    "jpn",
    "kor",
    "ara",
    "hin",
    "heb",
    "script/Latin",
    "script/Cyrillic",
}


def test_language_files_cover_the_fast_set() -> None:
    codes = set(installed_ocr_languages())
    assert _BROAD_SET <= codes
    assert len(codes) >= 100


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
