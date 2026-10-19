from __future__ import annotations

from dataclasses import replace

import pytest

from pymupdf4llm_service.picture_boxes import CandidateResult
from pymupdf4llm_service import text_repair as repair
from scripts import prototype_text_repair as prototype


def _line(
    x: float,
    y: float,
    text: str,
    *,
    width: float = 80,
) -> repair.TextLine:
    return repair.TextLine((x, y, x + width, y + 10), text, 10, False, False)


def test_join_span_text_preserves_word_boundaries() -> None:
    assert repair.join_span_text([{"text": "Right"}, {"text": "Joystick"}]) == (
        "Right Joystick"
    )
    assert repair.join_span_text([{"text": "TX1205592—"}, {"text": "UN"}]) == (
        "TX1205592—UN"
    )


def test_markdown_keeps_two_legend_columns_separate_and_joins_wraps() -> None:
    markdown = repair.lines_to_markdown(
        [
            _line(10, 10, "1—Power Button"),
            _line(10, 25, "2—Audio and Menu"),
            _line(20, 36, "Adjustment Button"),
            _line(150, 10, "12—Decrease Manual Tune"),
            _line(160, 21, "Frequency Button"),
        ],
        (0, 0, 260, 100),
    )

    assert markdown.splitlines() == [
        "1— Power Button",
        "2— Audio and Menu Adjustment Button",
        "",
        "12— Decrease Manual Tune Frequency Button",
    ]
    assert "**" not in markdown


def test_boundary_check_rejects_changes_outside_source_ranges() -> None:
    replacements = [(7, 10, "NEW")]
    assert prototype.changes_confined_to_ranges(
        "before OLD after", "before NEW after", replacements
    )
    assert not prototype.changes_confined_to_ranges(
        "before OLD after", "changed NEW after", replacements
    )


@pytest.mark.parametrize("reason", ["text_only", "shrunk"])
def test_repair_replaces_only_eligible_picture_text(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    candidate = CandidateResult(0, (0, 0, 100, 100), None, reason)
    if reason == "shrunk":
        candidate = replace(candidate, final_bbox=(50, 0, 100, 100))
    monkeypatch.setattr(repair, "refine_candidates", lambda **_: [candidate])
    monkeypatch.setattr(repair, "extract_text_lines", lambda _: [_line(10, 10, "Legend")])
    monkeypatch.setattr(repair, "embedded_raster_boxes", lambda _: [])
    monkeypatch.setattr(repair, "useful_path_boxes", lambda *_: [])

    page = type("Page", (), {"rect": (0, 0, 100, 100), "get_drawings": lambda _: []})()
    repaired = repair.repair_picture_text(
        page,
        {
            "text": "before OLD after",
            "page_boxes": [{"class": "picture", "bbox": [0, 0, 100, 100], "pos": [7, 10]}],
        },
    )

    assert "OLD" not in repaired
    assert "Legend" in repaired


def test_raster_candidate_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = CandidateResult(
        0, (0, 0, 100, 100), (0, 0, 100, 100), "raster"
    )
    monkeypatch.setattr(repair, "refine_candidates", lambda **_: [candidate])
    monkeypatch.setattr(repair, "extract_text_lines", lambda _: [_line(10, 10, "Legend")])
    monkeypatch.setattr(repair, "embedded_raster_boxes", lambda _: [(0, 0, 100, 100)])
    monkeypatch.setattr(repair, "useful_path_boxes", lambda *_: [])

    page = type("Page", (), {"rect": (0, 0, 100, 100), "get_drawings": lambda _: []})()
    repaired, decisions, _ = repair.repair_picture_text_audited(
        page,
        {
            "text": "before OLD after",
            "page_boxes": [{"class": "picture", "bbox": [0, 0, 100, 100], "pos": [7, 10]}],
        },
    )

    assert repaired == "before OLD after"
    assert decisions[0]["reason"] == "raster"


def test_empty_picture_candidate_does_not_change_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = CandidateResult(0, (0, 0, 100, 100), None, "text_only")
    monkeypatch.setattr(repair, "refine_candidates", lambda **_: [candidate])
    monkeypatch.setattr(repair, "extract_text_lines", lambda _: [])
    monkeypatch.setattr(repair, "embedded_raster_boxes", lambda _: [])
    monkeypatch.setattr(repair, "useful_path_boxes", lambda *_: [])

    page = type("Page", (), {"rect": (0, 0, 100, 100), "get_drawings": lambda _: []})()
    before = "before\n\n\nafter"
    repaired = repair.repair_picture_text(
        page,
        {
            "text": before,
            "page_boxes": [{"class": "picture", "bbox": [0, 0, 100, 100], "pos": [7, 9]}],
        },
    )

    assert repaired == before
