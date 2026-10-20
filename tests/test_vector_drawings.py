from __future__ import annotations

import pymupdf

from pymupdf4llm_service.vector_drawings import (
    VectorDrawingSettings,
    detect_vector_drawings,
)


def _page() -> tuple[pymupdf.Document, pymupdf.Page]:
    document = pymupdf.open()
    return document, document.new_page(width=400, height=500)


def test_uncovered_drawing_is_found_by_fallback() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(40, 50, 180, 160), width=2)
        page.draw_line((50, 60), (170, 150), width=2)

        found = detect_vector_drawings(page, [])

        assert len(found) == 1
        assert found[0]["detector"] == "fallback"
    finally:
        document.close()


def test_candidate_with_two_figure_ids_is_split() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(80, 40, 300, 130), width=2)
        page.draw_line((90, 50), (290, 120), width=2)
        page.insert_text((80, 150), "TX100001 —UN—01JAN20")
        page.draw_rect(pymupdf.Rect(80, 250, 300, 340), width=2)
        page.draw_line((90, 260), (290, 330), width=2)
        page.insert_text((80, 360), "TX100002 —UN—01JAN20")

        found = detect_vector_drawings(page, [(40, 20, 340, 380)])

        assert len(found) == 2
        assert all(item["detector"] == "layout" for item in found)
        assert found[0]["bbox"][3] < found[1]["bbox"][1]
    finally:
        document.close()


def test_raster_inside_layout_candidate_is_not_duplicated() -> None:
    document, page = _page()
    try:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 120), 0)
        pixmap.set_rect(pixmap.irect, (20, 30, 40))
        page.insert_image(
            pymupdf.Rect(40, 40, 200, 200),
            stream=pixmap.tobytes("png"),
        )

        assert detect_vector_drawings(page, [(40, 40, 200, 200)]) == []
    finally:
        document.close()


def test_ruled_table_without_figure_id_is_not_a_drawing() -> None:
    document, page = _page()
    try:
        for x in (40, 140, 240, 340):
            page.draw_line((x, 80), (x, 260), width=1)
        for y in (80, 140, 200, 260):
            page.draw_line((40, y), (340, y), width=1)
        for row, y in enumerate((115, 175, 235), start=1):
            page.insert_text((60, y), f"row {row}")
            page.insert_text((160, y), "value")

        assert detect_vector_drawings(page, []) == []
    finally:
        document.close()


def test_page_edge_drawing_is_clipped() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(0, 0, 80, 70), width=2)
        page.draw_line((0, 70), (80, 0), width=2)

        found = detect_vector_drawings(page, [])

        assert len(found) == 1
        assert found[0]["bbox"][0] == 0
        assert found[0]["bbox"][1] == 0
    finally:
        document.close()


def test_fallback_fragment_is_absorbed_into_the_layout_drawing() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(40, 80, 70, 170), width=2)
        page.draw_rect(pymupdf.Rect(80, 80, 280, 170), width=2)
        page.draw_line((90, 90), (270, 160), width=2)

        found = detect_vector_drawings(page, [(80, 80, 280, 170)])

        assert len(found) == 1
        assert found[0]["bbox"][0] <= 42
    finally:
        document.close()


def test_tiny_line_is_ignored() -> None:
    document, page = _page()
    try:
        page.draw_line((40, 40), (50, 40), width=1)

        assert detect_vector_drawings(page, []) == []
    finally:
        document.close()


def test_nearby_fragments_of_one_figure_are_merged() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(30, 80, 120, 180), width=2)
        page.draw_rect(pymupdf.Rect(155, 80, 245, 180), width=2)

        found = detect_vector_drawings(page, [])

        assert len(found) == 1
        assert found[0]["bbox"][0] < 30
        assert found[0]["bbox"][2] > 245
    finally:
        document.close()


def test_nearby_figures_with_different_ids_stay_separate() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(30, 80, 120, 180), width=2)
        page.insert_text((35, 200), "TX100001")
        page.draw_rect(pymupdf.Rect(155, 80, 245, 180), width=2)
        page.insert_text((160, 200), "TX100002")

        found = detect_vector_drawings(page, [])

        assert len(found) == 2
    finally:
        document.close()


def test_thresholds_and_figure_id_patterns_load_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VECTOR_MERGE_HORIZONTAL_GAP", "42.5")
    monkeypatch.setenv("VECTOR_PREFILTER_MIN_PATHS", "3")
    monkeypatch.setenv(
        "VECTOR_FIGURE_ID_PATTERNS",
        r"\bFIG-\d+;;\bDWG-\d+",
    )

    settings = VectorDrawingSettings.from_environment()

    assert settings.merge_horizontal_gap == 42.5
    assert settings.prefilter_min_paths == 3
    assert settings.figure_id_patterns == (r"\bFIG-\d+", r"\bDWG-\d+")


def test_custom_figure_id_pattern_splits_candidate() -> None:
    document, page = _page()
    try:
        page.draw_rect(pymupdf.Rect(80, 40, 300, 130), width=2)
        page.insert_text((80, 150), "FIG-100")
        page.draw_rect(pymupdf.Rect(80, 250, 300, 340), width=2)
        page.insert_text((80, 360), "FIG-200")
        settings = VectorDrawingSettings(figure_id_patterns=(r"\bFIG-\d+",))

        found = detect_vector_drawings(
            page,
            [(40, 20, 340, 380)],
            settings,
        )

        assert len(found) == 2
        assert all(item["id_split"] for item in found)
    finally:
        document.close()
