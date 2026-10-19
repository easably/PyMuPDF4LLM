from __future__ import annotations

from pymupdf4llm_service.picture_boxes import (
    RefinementSettings,
    is_frame_path,
    refine_candidates,
)


def _path(bbox: list[float]) -> dict[str, object]:
    return {"rect": bbox}


def test_raster_match_is_not_rewritten_as_vector() -> None:
    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[20, 20, 120, 120]],
        paths=[],
        text_spans=[],
        raster_boxes=[[20, 20, 120, 120]],
    )[0]

    assert result.reason == "raster"
    assert result.final_bbox == (20, 20, 120, 120)


def test_candidate_containing_substantial_raster_is_not_rewritten() -> None:
    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[10, 10, 190, 190]],
        paths=[_path([20, 120, 180, 180])],
        text_spans=[],
        raster_boxes=[[20, 20, 180, 110]],
    )[0]

    assert result.reason == "raster"
    assert result.final_bbox == (20, 20, 180, 110)


def test_text_only_candidate_is_rejected() -> None:
    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[20, 20, 120, 80]],
        paths=[],
        text_spans=[{"bbox": [25, 25, 100, 35], "text": "legend"}],
        raster_boxes=[],
    )[0]

    assert result.reason == "text_only"
    assert result.final_bbox is None


def test_candidate_with_procedure_text_shrinks_to_vector_area() -> None:
    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[10, 10, 190, 150]],
        paths=[_path([110, 20, 170, 100])],
        text_spans=[
            {"bbox": [20, 20, 90, 30], "text": "procedure"},
            {"bbox": [115, 25, 125, 35], "text": "1"},
        ],
        raster_boxes=[],
    )[0]

    assert result.reason == "shrunk"
    assert result.final_bbox is not None
    assert result.final_bbox[0] > 100
    assert result.final_bbox[2] < 180


def test_frame_is_measured_but_not_removed_from_input() -> None:
    frame = [5, 5, 195, 195]
    assert is_frame_path(frame, [0, 0, 200, 200])

    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[5, 5, 195, 195]],
        paths=[_path(frame), _path([80, 60, 140, 130])],
        text_spans=[],
        raster_boxes=[],
    )[0]

    assert result.reason == "shrunk"
    assert result.measurements["useful_path_count"] == 1
    assert result.final_bbox is not None
    assert result.final_bbox[0] > 70


def test_tiny_result_is_rejected() -> None:
    result = refine_candidates(
        page_bbox=[0, 0, 200, 200],
        candidate_boxes=[[10, 10, 100, 100]],
        paths=[_path([50, 50, 53, 53])],
        text_spans=[],
        raster_boxes=[],
    )[0]

    assert result.reason == "too_small"
    assert result.final_bbox is None


def test_near_page_sized_result_is_rejected() -> None:
    settings = RefinementSettings(
        maximum_page_area_share=0.5,
        frame_min_width_share=2,
        frame_min_height_share=2,
    )
    result = refine_candidates(
        page_bbox=[0, 0, 100, 100],
        candidate_boxes=[[0, 0, 100, 100]],
        paths=[_path([5, 5, 95, 95])],
        text_spans=[],
        raster_boxes=[],
        settings=settings,
    )[0]

    assert result.reason == "frame_sized"
    assert result.final_bbox is None

