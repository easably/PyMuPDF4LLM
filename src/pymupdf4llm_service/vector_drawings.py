"""Detect vector drawing regions from layout boxes and uncovered PDF paths."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from pymupdf4llm_service.picture_boxes import (
    RectTuple,
    RefinementSettings,
    bbox_iou,
    embedded_raster_boxes,
    extract_text_spans,
    is_frame_path,
    refine_candidates,
)

_CAPTION_PATTERN = re.compile(
    r"(?i)\b(?:illustration|figure|fig\.?)\s*\d+"
)
_CHROME_LAYOUT_CLASSES = frozenset({"page-header", "page-footer"})


@dataclass(frozen=True)
class VectorDrawingSettings:
    """Detection thresholds; every field can be overridden by environment."""

    minimum_side: float = 18.0
    minimum_paths: int = 1
    maximum_page_area_share: float = 0.75
    fallback_maximum_page_area_share: float = 0.28
    table_overlap_share: float = 0.65
    covered_path_share: float = 0.6
    duplicate_iou: float = 0.65
    margin: float = 2.0
    cluster_x_tolerance: float = 3.0
    cluster_y_tolerance: float = 3.0
    fallback_table_min_width_share: float = 0.35
    fallback_table_min_area_share: float = 0.08
    fallback_table_without_id_overlap_share: float = 0.2
    fallback_figure_id_margin: float = 150.0
    merge_figure_id_margin: float = 100.0
    merge_horizontal_gap: float = 85.0
    merge_vertical_gap: float = 10.0
    merge_overlap_share: float = 0.35
    split_vertical_end_offset: float = 4.0
    split_vertical_start_offset: float = 16.0
    split_horizontal_end_offset: float = 4.0
    split_horizontal_start_offset: float = 4.0
    split_margin: float = 8.0
    split_path_min_extent: float = 2.0
    prefilter_min_paths: int = 1
    edge_band_share: float = 0.08
    caption_margin: float = 36.0
    figure_id_patterns: tuple[str, ...] = (r"\b(?:TX|TS)\d{5,}",)

    @classmethod
    def from_environment(cls) -> "VectorDrawingSettings":
        defaults = cls()
        values: dict[str, Any] = {}
        for field_name, field_type in _NUMERIC_SETTING_FIELDS.items():
            env_name = f"VECTOR_{field_name.upper()}"
            raw = os.environ.get(env_name)
            if raw is None:
                continue
            try:
                values[field_name] = field_type(raw)
            except ValueError:
                values[field_name] = getattr(defaults, field_name)
        raw_patterns = os.environ.get("VECTOR_FIGURE_ID_PATTERNS")
        if raw_patterns is not None:
            patterns = tuple(item.strip() for item in raw_patterns.split(";;") if item.strip())
            if patterns:
                values["figure_id_patterns"] = patterns
        return cls(**values)


_NUMERIC_SETTING_FIELDS: dict[str, type[int] | type[float]] = {
    "minimum_side": float,
    "minimum_paths": int,
    "maximum_page_area_share": float,
    "fallback_maximum_page_area_share": float,
    "table_overlap_share": float,
    "covered_path_share": float,
    "duplicate_iou": float,
    "margin": float,
    "cluster_x_tolerance": float,
    "cluster_y_tolerance": float,
    "fallback_table_min_width_share": float,
    "fallback_table_min_area_share": float,
    "fallback_table_without_id_overlap_share": float,
    "fallback_figure_id_margin": float,
    "merge_figure_id_margin": float,
    "merge_horizontal_gap": float,
    "merge_vertical_gap": float,
    "merge_overlap_share": float,
    "split_vertical_end_offset": float,
    "split_vertical_start_offset": float,
    "split_horizontal_end_offset": float,
    "split_horizontal_start_offset": float,
    "split_margin": float,
    "split_path_min_extent": float,
    "prefilter_min_paths": int,
    "edge_band_share": float,
    "caption_margin": float,
}


def detect_vector_drawings(
    page: Any,
    layout_boxes: Sequence[Sequence[float]],
    settings: VectorDrawingSettings | None = None,
    *,
    raster_boxes: Sequence[Sequence[float]] | None = None,
    paths: Sequence[dict[str, Any]] | None = None,
    page_boxes: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return vector boxes with ``layout`` or ``fallback`` detector labels."""
    config = settings or VectorDrawingSettings.from_environment()
    page_box = _rect(page.rect)
    paths = list(paths) if paths is not None else page.get_drawings()
    text_spans = extract_text_spans(page)
    rasters = (
        [_rect(box) for box in raster_boxes]
        if raster_boxes is not None
        else embedded_raster_boxes(page)
    )
    useful_paths = useful_vector_paths(page, paths)
    chrome_boxes, caption_boxes = _layout_support_boxes(page_boxes)

    refined = refine_candidates(
        page_bbox=page_box,
        candidate_boxes=layout_boxes,
        paths=paths,
        text_spans=text_spans,
        raster_boxes=rasters,
    )
    found: list[dict[str, Any]] = []
    layout_regions: list[tuple[RectTuple, bool]] = []
    rejected_layout: list[RectTuple] = []
    for result in refined:
        if result.reason in {"text_only", "too_small", "frame_sized"}:
            rejected_layout.append(result.original_bbox)
            continue
        if result.reason not in {"accepted", "shrunk"} or result.final_bbox is None:
            continue
        split = _split_on_figure_ids(
            result.original_bbox,
            result.final_bbox,
            useful_paths,
            text_spans,
            config,
        )
        for box in split:
            layout_regions.append((box, len(split) > 1))
    merged_layout = _merge_fragments(
        [box for box, _split in layout_regions],
        text_spans,
        config,
    )
    for box in merged_layout:
        if _reject_chrome_or_edge(
            box,
            page_box,
            chrome_boxes,
            caption_boxes,
            text_spans,
            config,
        ):
            continue
        source_regions = [
            (source, split_by_id)
            for source, split_by_id in layout_regions
            if _intersects(source, box)
        ]
        id_split = any(split_by_id for _source, split_by_id in source_regions)
        id_merged = (
            len(source_regions) > 1
            and len(
                _nearby_figure_ids(
                    box,
                    text_spans,
                    config.merge_figure_id_margin,
                    config,
                )
            )
            == 1
        )
        _append_distinct(
            found,
            box,
            "layout",
            config,
            id_split=id_split,
            id_merged=id_merged,
        )

    covered = [item["bbox"] for item in found] + list(rasters)
    uncovered = [
        path
        for path in useful_paths
        if not any(
            _center_in(_rect(path["rect"]), _rect(box))
            or _intersection_share(_rect(path["rect"]), _rect(box))
            >= config.covered_path_share
            for box in covered
        )
    ]
    if uncovered:
        clusters = page.cluster_drawings(
            drawings=uncovered,
            x_tolerance=config.cluster_x_tolerance,
            y_tolerance=config.cluster_y_tolerance,
        )
        merged_clusters = _merge_fragments(
            [_rect(item) for item in clusters], text_spans, config
        )
        table_boxes: list[RectTuple] | None = None
        for raw_cluster in merged_clusters:
            cluster = _clip(_expand(raw_cluster, config.margin), page_box)
            path_count = sum(
                1 for path in uncovered if _intersects(_rect(path["rect"]), cluster)
            )
            if not _valid_box(cluster, page_box, path_count, config):
                continue
            if (
                _area(cluster) / max(_area(page_box), 1)
                >= config.fallback_maximum_page_area_share
            ):
                continue
            if any(
                _intersection_share(cluster, rejected) >= config.covered_path_share
                for rejected in rejected_layout
            ):
                # Layout already said this region is not a drawing; do not
                # resurrect it when find_tables() is empty after pymupdf.layout.
                continue
            page_width = page_box[2] - page_box[0]
            page_area = _area(page_box)
            table_sized = (
                cluster[2] - cluster[0]
                >= page_width * config.fallback_table_min_width_share
                or _area(cluster) >= page_area * config.fallback_table_min_area_share
            )
            if table_boxes is None:
                # Prefer find_tables() only. Layout "table" boxes on this
                # corpus are often huge page bands that cover real drawings.
                table_boxes = _table_boxes(page)
            table_share = max(
                (_intersection_share(cluster, table) for table in table_boxes),
                default=0.0,
            )
            nearby_ids = _nearby_figure_ids(
                cluster,
                text_spans,
                config.fallback_figure_id_margin,
                config,
            )
            if (
                table_share >= config.table_overlap_share
                and table_sized
            ) or (
                table_share >= config.fallback_table_without_id_overlap_share
                and not nearby_ids
            ):
                continue
            if any(
                _intersection_share(cluster, raster) >= config.covered_path_share
                for raster in rasters
            ):
                continue
            if _reject_chrome_or_edge(
                cluster,
                page_box,
                chrome_boxes,
                caption_boxes,
                text_spans,
                config,
            ):
                continue
            _add_or_merge(found, cluster, "fallback", config, text_spans)

    # Drop mid-page logo marks: small area, no figure id or caption.
    # Thin wide drawings (bolt-head strips) keep a large area and survive.
    found = [
        item
        for item in found
        if not (
            _area(_rect(item["bbox"])) < 4000.0
            and not _has_drawing_evidence(
                _rect(item["bbox"]), caption_boxes, text_spans, config
            )
        )
    ]
    found.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return found


def vector_candidates(
    document: Any,
    chunks_by_page: dict[int, dict[str, Any]],
    pages: list[int],
) -> list[dict[str, Any]]:
    """Acceptance-runner adapter."""
    output: list[dict[str, Any]] = []
    for page_number in pages:
        page = document[page_number - 1]
        page_boxes = chunks_by_page[page_number].get("page_boxes") or []
        layout_boxes = [
            box["bbox"]
            for box in page_boxes
            if box.get("class") == "picture"
        ]
        for index, item in enumerate(
            detect_vector_drawings(
                page,
                layout_boxes,
                raster_boxes=[],
                page_boxes=page_boxes,
            )
        ):
            output.append(
                {
                    "id": f"vector-p{page_number:03d}-{index}",
                    "page": page_number,
                    "bbox": list(item["bbox"]),
                    "detector": item["detector"],
                }
            )
    return output


def all_picture_candidates(
    document: Any,
    chunks_by_page: dict[int, dict[str, Any]],
    pages: list[int],
) -> list[dict[str, Any]]:
    """Acceptance adapter returning embedded raster and vector boxes."""
    output: list[dict[str, Any]] = []
    for page_number in pages:
        page = document[page_number - 1]
        raster_boxes = embedded_raster_boxes(page)
        for index, box in enumerate(raster_boxes):
            output.append(
                {
                    "id": f"raster-p{page_number:03d}-{index}",
                    "page": page_number,
                    "bbox": list(box),
                    "detector": "embedded_raster",
                }
            )
        page_boxes = chunks_by_page[page_number].get("page_boxes") or []
        layout_boxes = [
            box["bbox"]
            for box in page_boxes
            if box.get("class") == "picture"
        ]
        vectors = detect_vector_drawings(
            page,
            layout_boxes,
            raster_boxes=raster_boxes,
            page_boxes=page_boxes,
        )
        for index, item in enumerate(vectors):
            output.append(
                {
                    "id": f"vector-p{page_number:03d}-{index}",
                    "page": page_number,
                    "bbox": list(item["bbox"]),
                    "detector": item["detector"],
                }
            )
    return output


def useful_vector_paths(
    page: Any,
    paths: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return non-frame paths with visible extent for the cheap prefilter."""
    page_box = _rect(page.rect)
    source = list(paths) if paths is not None else page.get_drawings()
    return [
        path
        for path in source
        if not is_frame_path(path["rect"], page_box)
        and _path_has_extent(path["rect"])
    ]


def _split_on_figure_ids(
    original: RectTuple,
    refined: RectTuple,
    paths: Sequence[dict[str, Any]],
    spans: Sequence[dict[str, Any]],
    settings: VectorDrawingSettings,
) -> list[RectTuple]:
    ids = sorted(
        (
            _rect(span["bbox"])
            for span in spans
            if _figure_id(str(span.get("text") or ""), settings)
            and _center_in(_rect(span["bbox"]), original)
        ),
        key=lambda box: box[1],
    )
    if len(ids) < 2:
        return [refined]

    split_margin = (
        settings.margin
        if all(box[3] - box[1] > box[2] - box[0] for box in ids)
        else max(settings.margin, settings.split_margin)
    )
    cuts: list[tuple[float, float]] = []
    for figure_id in ids[:-1]:
        width = figure_id[2] - figure_id[0]
        height = figure_id[3] - figure_id[1]
        if height > width:
            cuts.append(
                (
                    figure_id[3] + settings.split_vertical_end_offset,
                    figure_id[3] + settings.split_vertical_start_offset,
                )
            )
        else:
            cuts.append(
                (
                    figure_id[1] - settings.split_horizontal_end_offset,
                    figure_id[1] + settings.split_horizontal_start_offset,
                )
            )
    bands: list[RectTuple] = []
    top = original[1]
    for bottom, next_top in cuts:
        bands.append((original[0], top, original[2], bottom))
        top = next_top
    bands.append((original[0], top, original[2], original[3]))

    boxes: list[RectTuple] = []
    for band in bands:
        band_paths = [
            _rect(path["rect"])
            for path in paths
            if _center_in(_rect(path["rect"]), band)
            and _center_in(_rect(path["rect"]), refined)
            and _rect(path["rect"])[2] - _rect(path["rect"])[0]
            >= settings.split_path_min_extent
            and _rect(path["rect"])[3] - _rect(path["rect"])[1]
            >= settings.split_path_min_extent
        ]
        if len(band_paths) < settings.minimum_paths:
            continue
        box = _clip(_expand(_union(band_paths), split_margin), band)
        if _valid_box(box, original, len(band_paths), settings):
            boxes.append(box)
    return boxes or [refined]


def _add_or_merge(
    found: list[dict[str, Any]],
    box: RectTuple,
    detector: str,
    settings: VectorDrawingSettings,
    text_spans: Sequence[dict[str, Any]],
) -> None:
    """Absorb a leftover cluster into a nearby picture, or append it."""
    for item in found:
        if _intersection_share(box, item["bbox"]) >= settings.covered_path_share:
            return
    for item in found:
        merged = _merge_fragments([item["bbox"], box], text_spans, settings)
        if len(merged) == 1:
            item["bbox"] = merged[0]
            return
    _append_distinct(found, box, detector, settings)


def _append_distinct(
    found: list[dict[str, Any]],
    box: RectTuple,
    detector: str,
    settings: VectorDrawingSettings,
    *,
    id_split: bool = False,
    id_merged: bool = False,
) -> None:
    if any(
        bbox_iou(item["bbox"], box) >= settings.duplicate_iou
        or _intersection_share(box, item["bbox"]) >= settings.covered_path_share
        for item in found
    ):
        return
    found.append(
        {
            "bbox": box,
            "detector": detector,
            "id_split": id_split,
            "id_merged": id_merged,
        }
    )


def _merge_fragments(
    clusters: list[RectTuple],
    text_spans: Sequence[dict[str, Any]],
    settings: VectorDrawingSettings,
) -> list[RectTuple]:
    merged: list[RectTuple] = []
    for cluster in sorted(clusters, key=lambda box: (box[1], box[0])):
        for index, current in enumerate(merged):
            horizontal_gap = max(0.0, max(current[0], cluster[0]) - min(current[2], cluster[2]))
            vertical_overlap = max(
                0.0, min(current[3], cluster[3]) - max(current[1], cluster[1])
            )
            vertical_gap = max(
                0.0, max(current[1], cluster[1]) - min(current[3], cluster[3])
            )
            horizontal_overlap = max(
                0.0, min(current[2], cluster[2]) - max(current[0], cluster[0])
            )
            minimum_width = max(
                1.0,
                min(current[2] - current[0], cluster[2] - cluster[0]),
            )
            minimum_height = max(
                1.0,
                min(current[3] - current[1], cluster[3] - cluster[1]),
            )
            combined = _union([current, cluster])
            distinct_ids = _nearby_figure_ids(
                combined,
                text_spans,
                settings.merge_figure_id_margin,
                settings,
            )
            same_row = (
                horizontal_gap <= settings.merge_horizontal_gap
                and vertical_overlap / minimum_height >= settings.merge_overlap_share
            )
            stacked_parts = (
                vertical_gap <= settings.merge_vertical_gap
                and horizontal_overlap / minimum_width >= settings.merge_overlap_share
            )
            if (
                (same_row or stacked_parts)
                and len(distinct_ids) < 2
            ):
                merged[index] = combined
                break
        else:
            merged.append(cluster)
    return merged


def _nearby_figure_ids(
    box: RectTuple,
    text_spans: Sequence[dict[str, Any]],
    margin: float,
    settings: VectorDrawingSettings,
) -> set[str]:
    found: set[str] = set()
    for span in text_spans:
        match = _figure_id(str(span.get("text") or ""), settings)
        if match is None:
            continue
        span_box = _rect(span["bbox"])
        center_x = (span_box[0] + span_box[2]) / 2
        if (
            box[0] - margin <= center_x <= box[2] + margin
            and box[1] - margin <= span_box[1] <= box[3] + margin
        ):
            found.add(match)
    return found


def _figure_id(text: str, settings: VectorDrawingSettings) -> str | None:
    for pattern in settings.figure_id_patterns:
        match = re.search(pattern, text)
        if match is not None:
            return match.group(0)
    return None


def _layout_support_boxes(
    page_boxes: Sequence[dict[str, Any]] | None,
) -> tuple[list[RectTuple], list[RectTuple]]:
    chrome: list[RectTuple] = []
    captions: list[RectTuple] = []
    for box in page_boxes or ():
        raw = box.get("bbox")
        if raw is None:
            continue
        rect = _rect(raw)
        class_name = str(box.get("class") or "")
        if class_name in _CHROME_LAYOUT_CLASSES:
            chrome.append(rect)
        elif class_name == "caption":
            captions.append(rect)
    return chrome, captions


def _has_drawing_evidence(
    box: RectTuple,
    caption_boxes: Sequence[RectTuple],
    text_spans: Sequence[dict[str, Any]],
    settings: VectorDrawingSettings,
) -> bool:
    if _nearby_figure_ids(
        box,
        text_spans,
        settings.fallback_figure_id_margin,
        settings,
    ):
        return True
    margin = settings.caption_margin
    expanded = _expand(box, margin)
    for caption in caption_boxes:
        if _intersects(expanded, caption):
            return True
    for span in text_spans:
        text = str(span.get("text") or "")
        if not _CAPTION_PATTERN.search(text):
            continue
        if _intersects(expanded, _rect(span["bbox"])):
            return True
    return False


def _center_in_edge_band(box: RectTuple, page_box: RectTuple, share: float) -> bool:
    page_height = page_box[3] - page_box[1]
    band = page_height * share
    center_y = (box[1] + box[3]) / 2
    return center_y <= page_box[1] + band or center_y >= page_box[3] - band


def _reject_chrome_or_edge(
    box: RectTuple,
    page_box: RectTuple,
    chrome_boxes: Sequence[RectTuple],
    caption_boxes: Sequence[RectTuple],
    text_spans: Sequence[dict[str, Any]],
    settings: VectorDrawingSettings,
) -> bool:
    """True when the box is page chrome without a figure id or caption."""
    in_chrome = any(_center_in(box, chrome) for chrome in chrome_boxes)
    in_edge = _center_in_edge_band(box, page_box, settings.edge_band_share)
    if not in_chrome and not in_edge:
        return False
    return not _has_drawing_evidence(box, caption_boxes, text_spans, settings)


def _valid_box(
    box: RectTuple,
    page_box: RectTuple,
    path_count: int,
    settings: VectorDrawingSettings,
) -> bool:
    return (
        path_count >= settings.minimum_paths
        and box[2] - box[0] >= settings.minimum_side
        and box[3] - box[1] >= settings.minimum_side
        and _area(box) / max(_area(page_box), 1) < settings.maximum_page_area_share
    )


def _table_boxes(page: Any) -> list[RectTuple]:
    try:
        return [_rect(table.bbox) for table in page.find_tables().tables]
    except Exception:
        return []


def _path_has_extent(raw: Sequence[float]) -> bool:
    box = _rect(raw)
    return box[2] > box[0] or box[3] > box[1]


def _rect(value: Sequence[float] | Iterable[float]) -> RectTuple:
    x0, y0, x1, y1 = map(float, value)
    return (x0, y0, x1, y1)


def _center_in(inner: RectTuple, outer: RectTuple) -> bool:
    x = (inner[0] + inner[2]) / 2
    y = (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _intersection_share(first: RectTuple, second: RectTuple) -> float:
    intersection = _intersection(first, second)
    return _area(intersection) / max(_area(first), 1)


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


def _area(box: RectTuple) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _expand(box: RectTuple, margin: float) -> RectTuple:
    return (
        box[0] - margin,
        box[1] - margin,
        box[2] + margin,
        box[3] + margin,
    )


def _clip(box: RectTuple, clip: RectTuple) -> RectTuple:
    return (
        max(box[0], clip[0]),
        max(box[1], clip[1]),
        min(box[2], clip[2]),
        min(box[3], clip[3]),
    )


def _union(boxes: Sequence[RectTuple]) -> RectTuple:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
