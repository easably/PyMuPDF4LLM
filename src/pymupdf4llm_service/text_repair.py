"""Repair text that layout incorrectly flattened into ``picture text``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from pymupdf4llm_service.picture_boxes import (
    RefinementSettings,
    embedded_raster_boxes,
    is_frame_path,
    refine_candidates,
)

_FIGURE_ID = re.compile(r"\b(?:TX|TS)\d{5,}")
_LEGEND = re.compile(r"^\s*\d{1,2}\s*[—-]\s*\D")
_CALLOUT_ONLY = re.compile(r"^\s*\d{1,2}(?:\s+\d{1,2})*\s*$")


@dataclass(frozen=True)
class TextLine:
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    bold: bool
    italic: bool


def repair_picture_text(page: Any, chunk: dict[str, Any]) -> str:
    """Return repaired Markdown without changing the source chunk."""
    markdown, _decisions, _replacements = repair_picture_text_audited(page, chunk)
    return markdown


def repair_picture_text_audited(
    page: Any, chunk: dict[str, Any]
) -> tuple[str, list[dict[str, Any]], list[tuple[int, int, str]]]:
    """Also return candidate decisions and source-range replacements."""
    markdown = str(chunk.get("text") or "")
    picture_boxes = [
        box
        for box in chunk.get("page_boxes") or []
        if box.get("class") == "picture"
    ]
    if not picture_boxes:
        return markdown, [], []

    paths = page.get_drawings()
    lines = extract_text_lines(page)
    results = refine_candidates(
        page_bbox=page.rect,
        candidate_boxes=[box["bbox"] for box in picture_boxes],
        paths=paths,
        text_spans=[{"bbox": list(line.bbox), "text": line.text} for line in lines],
        raster_boxes=embedded_raster_boxes(page),
    )
    vectors = useful_path_boxes(page.rect, paths)
    replacements: list[tuple[int, int, str]] = []
    decisions: list[dict[str, Any]] = []
    for box, result in zip(picture_boxes, results):
        decisions.append(result.as_dict())
        if result.reason not in {"text_only", "shrunk"}:
            continue
        selected = text_lines_outside_graphic(
            lines,
            result.original_bbox,
            vectors,
            text_only=result.reason == "text_only",
        )
        restored = lines_to_markdown(selected, result.original_bbox)
        start, end = map(int, box["pos"])
        if not restored and not markdown[start:end].strip():
            continue
        replacements.append((start, end, f"\n\n{restored}\n\n" if restored else "\n"))
    for start, end, replacement in sorted(replacements, reverse=True):
        markdown = markdown[:start] + replacement + markdown[end:]
    return markdown, decisions, replacements


def extract_text_lines(page: Any, span_gap: float = 10.0) -> list[TextLine]:
    output: list[TextLine] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for raw_line in block.get("lines", []):
            spans = [
                span
                for span in raw_line.get("spans", [])
                if str(span.get("text") or "").strip()
            ]
            spans.sort(key=lambda span: float(span["bbox"][0]))
            groups: list[list[dict[str, Any]]] = []
            for span in spans:
                if (
                    not groups
                    or float(span["bbox"][0]) - float(groups[-1][-1]["bbox"][2])
                    > span_gap
                ):
                    groups.append([span])
                else:
                    groups[-1].append(span)
            for group in groups:
                bbox = (
                    min(float(span["bbox"][0]) for span in group),
                    min(float(span["bbox"][1]) for span in group),
                    max(float(span["bbox"][2]) for span in group),
                    max(float(span["bbox"][3]) for span in group),
                )
                fonts = " ".join(str(span.get("font") or "") for span in group).lower()
                flags = [int(span.get("flags") or 0) for span in group]
                output.append(
                    TextLine(
                        bbox=bbox,
                        text=join_span_text(group),
                        size=max(float(span.get("size") or 0) for span in group),
                        bold="bold" in fonts or any(flag & 16 for flag in flags),
                        italic=(
                            "italic" in fonts
                            or "oblique" in fonts
                            or any(flag & 2 for flag in flags)
                        ),
                    )
                )
    return output


def useful_path_boxes(
    page_bbox: Sequence[float],
    paths: Sequence[dict[str, Any]],
    settings: RefinementSettings | None = None,
) -> list[tuple[float, float, float, float]]:
    config = settings or RefinementSettings()
    output: list[tuple[float, float, float, float]] = []
    for path in paths:
        rect = tuple(float(value) for value in path["rect"])
        if is_frame_path(rect, page_bbox, config):
            continue
        if (
            rect[2] - rect[0] >= config.useful_path_min_side
            and rect[3] - rect[1] >= config.useful_path_min_side
        ):
            output.append(rect)
    return output


def text_lines_outside_graphic(
    lines: Sequence[TextLine],
    candidate_bbox: Sequence[float],
    vector_boxes: Sequence[Sequence[float]],
    *,
    text_only: bool,
) -> list[TextLine]:
    return [
        line
        for line in lines
        if _center_in(line.bbox, candidate_bbox)
        and (text_only or not _line_is_graphic_text(line, vector_boxes))
    ]


def lines_to_markdown(
    lines: Sequence[TextLine], candidate_bbox: Sequence[float]
) -> str:
    if not lines:
        return ""
    rendered: list[str] = []
    for column in _columns(lines, candidate_bbox):
        previous_bottom: float | None = None
        ordered = sorted(column, key=lambda item: (item.bbox[1], item.bbox[0]))
        for line in _merge_wrapped_legend_lines(ordered):
            if previous_bottom is not None and line.bbox[1] - previous_bottom > line.size * 1.4:
                rendered.append("")
            rendered.append(_format_line(line))
            previous_bottom = max(previous_bottom or line.bbox[3], line.bbox[3])
        rendered.append("")
    return "\n".join(rendered).strip()


def join_span_text(spans: Sequence[dict[str, Any]]) -> str:
    text = ""
    for span in spans:
        addition = str(span.get("text") or "")
        if (
            text
            and addition
            and not text[-1].isspace()
            and not addition[0].isspace()
            and addition[0] not in ".,:;!?)]}"
            and text[-1] not in "([{/—-"
        ):
            text += " "
        text += addition
    return text.strip()


def _line_is_graphic_text(
    line: TextLine,
    vector_boxes: Sequence[Sequence[float]],
    margin: float = 10.0,
) -> bool:
    text = line.text.strip()
    if (
        not text
        or _FIGURE_ID.search(text)
        or _LEGEND.match(text)
        or line.italic
        or text.startswith(("•", "-", "NOTE:", "LOOK:", "LISTEN:"))
        or len(text) > 40
    ):
        return False
    near_vector = any(_intersects(line.bbox, _expand(box, margin)) for box in vector_boxes)
    return near_vector and (
        bool(_CALLOUT_ONLY.match(text))
        or len(text) <= 24
        or any(character.isdigit() for character in text)
    )


def _format_line(line: TextLine) -> str:
    text = line.text.strip()
    if _LEGEND.match(text):
        return re.sub(r"^(\d{1,2}\s*[—-])\s*", r"\1 ", text)
    if text.startswith("•"):
        return f"- {text[1:].strip()}"
    if line.bold and len(text) <= 120:
        return f"**{text}**"
    if line.italic:
        return f"*{text}*"
    return text


def _merge_wrapped_legend_lines(lines: Sequence[TextLine]) -> list[TextLine]:
    merged: list[TextLine] = []
    for line in lines:
        if (
            merged
            and _LEGEND.match(merged[-1].text)
            and not _LEGEND.match(line.text)
            and line.bbox[1] - merged[-1].bbox[3]
            <= max(line.size, merged[-1].size) * 0.8
        ):
            previous = merged[-1]
            merged[-1] = TextLine(
                (
                    min(previous.bbox[0], line.bbox[0]),
                    previous.bbox[1],
                    max(previous.bbox[2], line.bbox[2]),
                    line.bbox[3],
                ),
                f"{previous.text} {line.text}",
                max(previous.size, line.size),
                previous.bold and line.bold,
                previous.italic and line.italic,
            )
        else:
            merged.append(line)
    return merged


def _columns(
    lines: Sequence[TextLine], candidate_bbox: Sequence[float]
) -> list[list[TextLine]]:
    if len(lines) < 4:
        return [list(lines)]
    starts = sorted({round(line.bbox[0], 1) for line in lines})
    gaps = [
        (starts[index + 1] - starts[index], starts[index], starts[index + 1])
        for index in range(len(starts) - 1)
    ]
    if not gaps:
        return [list(lines)]
    gap, left, right = max(gaps)
    width = float(candidate_bbox[2]) - float(candidate_bbox[0])
    if gap < max(35.0, width * 0.12):
        return [list(lines)]
    split = (left + right) / 2
    return [
        column
        for column in (
            [line for line in lines if line.bbox[0] < split],
            [line for line in lines if line.bbox[0] >= split],
        )
        if column
    ]


def _center_in(inner: Sequence[float], outer: Sequence[float]) -> bool:
    x = (float(inner[0]) + float(inner[2])) / 2
    y = (float(inner[1]) + float(inner[3])) / 2
    return float(outer[0]) <= x <= float(outer[2]) and float(outer[1]) <= y <= float(
        outer[3]
    )


def _expand(rect: Sequence[float], margin: float) -> tuple[float, float, float, float]:
    return (
        float(rect[0]) - margin,
        float(rect[1]) - margin,
        float(rect[2]) + margin,
        float(rect[3]) + margin,
    )


def _intersects(first: Sequence[float], second: Sequence[float]) -> bool:
    return (
        min(float(first[2]), float(second[2]))
        > max(float(first[0]), float(second[0]))
        and min(float(first[3]), float(second[3]))
        > max(float(first[1]), float(second[1]))
    )
