"""Pure checks for the compact-loader acceptance runner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "accept_compact_track_loader.py"
_SPEC = importlib.util.spec_from_file_location("accept_compact_track_loader", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
accept = importlib.util.module_from_spec(_SPEC)
sys.modules["accept_compact_track_loader"] = accept
_SPEC.loader.exec_module(accept)


def test_checked_in_labels_are_valid_and_cover_reference_pages() -> None:
    root = Path(__file__).resolve().parents[1]
    labels = json.loads(
        (root / "acceptance" / "compact-track-loader" / "labels.json").read_text()
    )

    accept.validate_labels(labels)

    labeled_pages = {item["page"] for item in labels["pages"]}
    assert set(labels["reference_pages"]) <= labeled_pages
    assert {item["group"] for item in labels["pages"]} == {
        "vector_only",
        "mixed",
        "negative",
    }
    by_page = {item["page"]: item for item in labels["pages"]}
    assert {page: len(by_page[page]["drawings"]) for page in (82, 83, 84, 85)} == {
        82: 3,
        83: 2,
        84: 2,
        85: 2,
    }


def test_iou_and_greedy_matching_are_one_to_one() -> None:
    drawings = [{"id": "drawing", "bbox": [0, 0, 10, 10]}]
    candidates = [
        {"bbox": [0, 0, 10, 10]},
        {"bbox": [0, 0, 9, 10]},
    ]

    matches = accept.greedy_matches(drawings, candidates, threshold=0.5)

    assert accept.bbox_iou(drawings[0]["bbox"], candidates[0]["bbox"]) == 1
    assert len(matches) == 1
    assert matches[0].candidate_index == 0


def test_duplicate_candidate_counts_as_a_false_picture() -> None:
    labels = {
        "pages": [
            {
                "page": 1,
                "group": "vector_only",
                "drawings": [{"id": "one", "bbox": [0, 0, 10, 10]}],
                "not_pictures": [],
                "text_phrases": [],
                "legend_lines": [],
            }
        ]
    }
    chunks = {1: {"text": ""}}
    candidates = [
        {"page": 1, "bbox": [0, 0, 10, 10]},
        {"page": 1, "bbox": [0, 0, 10, 10]},
    ]

    result = accept.evaluate(labels, chunks, candidates, threshold=0.5)

    assert result["groups"]["vector_only"]["matches"] == 1
    assert result["groups"]["vector_only"]["false"] == 1
    assert result["groups"]["vector_only"]["precision"] == 0.5


def test_false_picture_uses_iou_not_simple_containment() -> None:
    candidate = {"bbox": [0, 0, 10, 10]}
    negatives = [{"type": "table", "bbox": [0, 0, 10, 10]}]

    assert accept.false_picture_type(candidate, negatives) == "table"
    contained = {"bbox": [2, 2, 8, 8]}
    assert accept.false_picture_type(contained, negatives) == "unclassified"


def test_phrase_location_distinguishes_picture_text() -> None:
    markdown = """
Normal heading
<!-- Start of picture text -->
Flattened procedure
<!-- End of picture text -->
"""
    assert accept.phrase_location(markdown, "Normal heading") == "normal"
    assert accept.phrase_location(markdown, "Flattened procedure") == "picture_text"
    assert accept.phrase_location(markdown, "Absent") == "missing"


def test_legend_line_reports_merged_columns() -> None:
    merged = """
<!-- Start of picture text -->
1— Power Button 12— Decrease Manual Tune<br>
<!-- End of picture text -->
"""
    separate = "1— Power Button\n12— Decrease Manual Tune\n"

    assert accept.legend_line_status(merged, "1— Power Button") == "merged"
    assert accept.legend_line_status(separate, "1— Power Button") == "normal_separate"

