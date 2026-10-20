"""Refine layout ``picture`` boxes without rendering or changing Markdown.

The functions in this module operate on plain dictionaries and tuples so the
heuristics can be tested without depending on the layout model.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

RectTuple = tuple[float, float, float, float]


@dataclass(frozen=True)
class RefinementSettings:
    raster_iou: float = 0.8
    raster_coverage: float = 0.9
    raster_min_candidate_share: float = 0.2
    minimum_side: float = 12.0
    maximum_page_area_share: float = 0.85
    useful_path_min_side: float = 2.0
    shrink_area_ratio: float = 0.9
    associated_text_margin: float = 8.0
    final_margin: float = 1.5
    frame_min_width_share: float = 0.7
    frame_min_height_share: float = 0.2
    thin_path_size: float = 1.0


@dataclass(frozen=True)
class CandidateResult:
    index: int
    original_bbox: RectTuple
    final_bbox: RectTuple | None
    reason: str
    measurements: dict[str, float | int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def refine_candidates(
    *,
    page_bbox: Sequence[float],
    candidate_boxes: Sequence[Sequence[float]],
    paths: Sequence[dict[str, Any]],
    text_spans: Sequence[dict[str, Any]],
    raster_boxes: Sequence[Sequence[float]],
    settings: RefinementSettings | None = None,
) -> list[CandidateResult]:
    """Return an accepted, shrunk, raster, or rejected result per candidate."""
    config = settings or RefinementSettings()
    page = _rect(page_bbox)
    path_data = [
        (_rect(path["rect"]), is_frame_path(path["rect"], page, config))
        for path in paths
    ]
    spans = [
        (_rect(span["bbox"]), str(span.get("text") or "").strip())
        for span in text_spans
        if str(span.get("text") or "").strip()
    ]
    rasters = [_rect(box) for box in raster_boxes]

    results: list[CandidateResult] = []
    for index, raw_box in enumerate(candidate_boxes):
        original = _clip(_rect(raw_box), page)
        raster = _best_raster_match(original, rasters, config)
        if raster is not None:
            results.append(
                CandidateResult(
                    index,
                    original,
                    raster,
                    "raster",
                    {"raster_iou": round(bbox_iou(original, raster), 6)},
                )
            )
            continue

        useful_paths = [
            rect
            for rect, frame in path_data
            if not frame
            and _intersects(rect, original)
            and rect[2] - rect[0] >= config.useful_path_min_side
            and rect[3] - rect[1] >= config.useful_path_min_side
        ]
        if not useful_paths:
            results.append(
                CandidateResult(
                    index,
                    original,
                    None,
                    "text_only",
                    {"useful_path_count": 0},
                )
            )
            continue

        vector_box = _union(useful_paths)
        nearby = _expand(vector_box, config.associated_text_margin)
        associated_spans = [
            rect
            for rect, text in spans
            if len(text) <= 40
            and _intersects(rect, original)
            and _intersects(rect, nearby)
        ]
        measured = _union([vector_box, *associated_spans])
        measured = _clip(_expand(measured, config.final_margin), original)
        width = measured[2] - measured[0]
        height = measured[3] - measured[1]
        measurements: dict[str, float | int] = {
            "useful_path_count": len(useful_paths),
            "associated_span_count": len(associated_spans),
            "vector_area_ratio": round(_area(vector_box) / max(_area(original), 1), 6),
            "final_area_ratio": round(_area(measured) / max(_area(original), 1), 6),
        }
        if width < config.minimum_side or height < config.minimum_side:
            results.append(
                CandidateResult(index, original, None, "too_small", measurements)
            )
            continue
        if _area(measured) / max(_area(page), 1) >= config.maximum_page_area_share:
            results.append(
                CandidateResult(index, original, None, "frame_sized", measurements)
            )
            continue

        changed = _area(measured) / max(_area(original), 1) < config.shrink_area_ratio
        results.append(
            CandidateResult(
                index,
                original,
                measured if changed else original,
                "shrunk" if changed else "accepted",
                measurements,
            )
        )
    return results


def is_frame_path(
    path_bbox: Sequence[float],
    page_bbox: Sequence[float],
    settings: RefinementSettings | None = None,
) -> bool:
    """Identify only page/section frame geometry; never mutate the path."""
    config = settings or RefinementSettings()
    rect = _rect(path_bbox)
    page = _rect(page_bbox)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    page_width = max(page[2] - page[0], 1)
    page_height = max(page[3] - page[1], 1)
    long_horizontal = (
        height <= config.thin_path_size
        and width / page_width >= config.frame_min_width_share
    )
    long_vertical = (
        width <= config.thin_path_size
        and height / page_height >= config.frame_min_height_share
    )
    enclosing = (
        width / page_width >= config.frame_min_width_share
        and height / page_height >= config.frame_min_height_share
    )
    return long_horizontal or long_vertical or enclosing


def extract_text_spans(page: Any) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text") or "")
                if text.strip():
                    spans.append({"bbox": list(span["bbox"]), "text": text})
    return spans


def min_picture_display_side_pt() -> float:
    try:
        value = float(os.environ.get("MIN_PICTURE_DISPLAY_SIDE_PT", "40"))
    except ValueError:
        return 40.0
    return value if value > 0 else 40.0


def embedded_raster_boxes(page: Any) -> list[RectTuple]:
    boxes: list[RectTuple] = []
    seen: set[RectTuple] = set()
    min_side = min_picture_display_side_pt()
    for image in page.get_images(full=True):
        xref = int(image[0])
        if int(image[2]) < 100 or int(image[3]) < 100:
            continue
        for rect in page.get_image_rects(xref):
            box = _rect(rect)
            if min(box[2] - box[0], box[3] - box[1]) < min_side:
                continue
            if box not in seen:
                seen.add(box)
                boxes.append(box)
    return boxes


def refined_layout_candidates(
    document: Any,
    chunks_by_page: dict[int, dict[str, Any]],
    pages: list[int],
) -> list[dict[str, Any]]:
    """Acceptance-runner adapter for raw layout candidates after refinement."""
    output: list[dict[str, Any]] = []
    for page_number in pages:
        page = document[page_number - 1]
        boxes = [
            box["bbox"]
            for box in chunks_by_page[page_number].get("page_boxes") or []
            if box.get("class") == "picture"
        ]
        results = refine_candidates(
            page_bbox=page.rect,
            candidate_boxes=boxes,
            paths=page.get_drawings(),
            text_spans=extract_text_spans(page),
            raster_boxes=embedded_raster_boxes(page),
        )
        for result in results:
            if result.final_bbox is None:
                continue
            output.append(
                {
                    "id": f"refined-p{page_number:03d}-{result.index}",
                    "page": page_number,
                    "bbox": list(result.final_bbox),
                    "detector": "refined_layout",
                    "reason": result.reason,
                }
            )
    return output


def bbox_iou(first: Sequence[float], second: Sequence[float]) -> float:
    a = _rect(first)
    b = _rect(second)
    intersection = _intersection(a, b)
    union = _area(a) + _area(b) - _area(intersection)
    return _area(intersection) / union if union else 0.0


def _best_raster_match(
    candidate: RectTuple,
    rasters: Sequence[RectTuple],
    settings: RefinementSettings,
) -> RectTuple | None:
    ranked = sorted(
        ((bbox_iou(candidate, raster), raster) for raster in rasters),
        key=lambda item: item[0],
        reverse=True,
    )
    if ranked and ranked[0][0] >= settings.raster_iou:
        return ranked[0][1]
    candidate_area = _area(candidate)
    for _iou, raster in ranked:
        intersection_area = _area(_intersection(candidate, raster))
        raster_area = _area(raster)
        if (
            raster_area
            and candidate_area
            and intersection_area / raster_area >= settings.raster_coverage
            and intersection_area / candidate_area
            >= settings.raster_min_candidate_share
        ):
            return raster
    return None


def _rect(value: Sequence[float] | Iterable[float]) -> RectTuple:
    x0, y0, x1, y1 = map(float, value)
    return (x0, y0, x1, y1)


def _area(rect: RectTuple) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection(first: RectTuple, second: RectTuple) -> RectTuple:
    return (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )


def _intersects(first: RectTuple, second: RectTuple) -> bool:
    intersection = _intersection(first, second)
    return intersection[2] > intersection[0] and intersection[3] > intersection[1]


def _union(rects: Sequence[RectTuple]) -> RectTuple:
    return (
        min(rect[0] for rect in rects),
        min(rect[1] for rect in rects),
        max(rect[2] for rect in rects),
        max(rect[3] for rect in rects),
    )


def _expand(rect: RectTuple, margin: float) -> RectTuple:
    return (
        rect[0] - margin,
        rect[1] - margin,
        rect[2] + margin,
        rect[3] + margin,
    )


def _clip(rect: RectTuple, clip: RectTuple) -> RectTuple:
    return (
        max(rect[0], clip[0]),
        max(rect[1], clip[1]),
        min(rect[2], clip[2]),
        min(rect[3], clip[3]),
    )
