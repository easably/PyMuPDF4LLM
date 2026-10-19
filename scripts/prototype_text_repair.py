#!/usr/bin/env python3
"""Prototype repair for text flattened into layout ``picture text`` blocks.

This script is deliberately not imported by the service. It writes before,
after, and unified-diff files for human review.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from pymupdf4llm_service.picture_boxes import (  # noqa: E402
    RefinementSettings,
    embedded_raster_boxes,
    is_frame_path,
    refine_candidates,
)
from pymupdf4llm_service.text_repair import (  # noqa: E402
    repair_picture_text_audited,
)

KNOWN_PDFS = {
    "ef29c72b6659a61f2d7905ffcbe7ec03375e45ae37c84a801a44f3940edcdf14": 220,
    "5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab": 530,
}
DEFAULT_PAGES = (68, 85, 181, 205, 2, 4, 9, 10, 13, 15, 16, 17, 18, 135)

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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_text_lines(page: Any, span_gap: float = 10.0) -> list[TextLine]:
    """Split PDF lines into visual groups when distant columns share a baseline."""
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
                text = _join_span_text(group)
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
                        text=text,
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
            rect[2] - rect[0] < config.useful_path_min_side
            or rect[3] - rect[1] < config.useful_path_min_side
        ):
            continue
        output.append(rect)
    return output


def line_is_graphic_text(
    line: TextLine,
    vector_boxes: Sequence[Sequence[float]],
    margin: float = 10.0,
) -> bool:
    """Keep only short text geometrically tied to drawing paths in the picture."""
    text = line.text.strip()
    if not text:
        return False
    if _FIGURE_ID.search(text) or _LEGEND.match(text):
        return False
    if line.italic or text.startswith(("•", "-", "NOTE:", "LOOK:", "LISTEN:")):
        return False
    if len(text) > 40:
        return False
    near_vector = any(_intersects(line.bbox, _expand(box, margin)) for box in vector_boxes)
    return near_vector and (
        bool(_CALLOUT_ONLY.match(text))
        or len(text) <= 24
        or any(character.isdigit() for character in text)
    )


def text_lines_outside_graphic(
    lines: Sequence[TextLine],
    candidate_bbox: Sequence[float],
    vector_boxes: Sequence[Sequence[float]],
    *,
    text_only: bool,
) -> list[TextLine]:
    selected: list[TextLine] = []
    for line in lines:
        if not _center_in(line.bbox, candidate_bbox):
            continue
        if text_only or not line_is_graphic_text(line, vector_boxes):
            selected.append(line)
    return selected


def lines_to_markdown(
    lines: Sequence[TextLine], candidate_bbox: Sequence[float]
) -> str:
    if not lines:
        return ""
    columns = _columns(lines, candidate_bbox)
    rendered: list[str] = []
    for column in columns:
        previous_bottom: float | None = None
        ordered = sorted(column, key=lambda item: (item.bbox[1], item.bbox[0]))
        for line in _merge_wrapped_legend_lines(ordered):
            if previous_bottom is not None and line.bbox[1] - previous_bottom > line.size * 1.4:
                rendered.append("")
            rendered.append(_format_line(line))
            previous_bottom = max(previous_bottom or line.bbox[3], line.bbox[3])
        rendered.append("")
    return "\n".join(rendered).strip()


def repair_page(page: Any, chunk: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    markdown, decisions, _boundary_ok = repair_page_audited(page, chunk)
    return markdown, decisions


def repair_page_audited(
    page: Any, chunk: dict[str, Any]
) -> tuple[str, list[dict[str, Any]], bool]:
    before = str(chunk.get("text") or "")
    markdown, decisions, replacements = repair_picture_text_audited(page, chunk)
    return markdown, decisions, changes_confined_to_ranges(before, markdown, replacements)


def changes_confined_to_ranges(
    before: str,
    after: str,
    replacements: Sequence[tuple[int, int, str]],
) -> bool:
    """Verify that text outside the replaced source ranges is byte-identical."""
    before_cursor = 0
    after_cursor = 0
    for start, end, replacement in sorted(replacements):
        unchanged = before[before_cursor:start]
        if after[after_cursor : after_cursor + len(unchanged)] != unchanged:
            return False
        after_cursor += len(unchanged)
        if after[after_cursor : after_cursor + len(replacement)] != replacement:
            return False
        after_cursor += len(replacement)
        before_cursor = end
    return before[before_cursor:] == after[after_cursor:]


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


def _join_span_text(spans: Sequence[dict[str, Any]]) -> str:
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


def _merge_wrapped_legend_lines(lines: Sequence[TextLine]) -> list[TextLine]:
    merged: list[TextLine] = []
    for line in lines:
        if (
            merged
            and _LEGEND.match(merged[-1].text)
            and not _LEGEND.match(line.text)
            and line.bbox[1] - merged[-1].bbox[3] <= max(line.size, merged[-1].size) * 0.8
        ):
            previous = merged[-1]
            merged[-1] = TextLine(
                bbox=(
                    min(previous.bbox[0], line.bbox[0]),
                    previous.bbox[1],
                    max(previous.bbox[2], line.bbox[2]),
                    line.bbox[3],
                ),
                text=f"{previous.text} {line.text}",
                size=max(previous.size, line.size),
                bold=previous.bold and line.bold,
                italic=previous.italic and line.italic,
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
    first = [line for line in lines if line.bbox[0] < split]
    second = [line for line in lines if line.bbox[0] >= split]
    return [column for column in (first, second) if column]


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf",
        type=Path,
        default=ROOT / "tessdata" / "pdfs" / "compact track loader.pdf",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pages", type=int, nargs="+")
    selection.add_argument("--all-pages", action="store_true")
    parser.add_argument(
        "--out", type=Path, default=ROOT / ".tmp" / "compact-labels" / "text-repair"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    actual_hash = file_sha256(args.pdf)
    expected_pages = KNOWN_PDFS.get(actual_hash)
    if expected_pages is None:
        print(f"Unknown PDF SHA-256: {actual_hash}", file=sys.stderr)
        return 2

    import pymupdf
    import pymupdf4llm

    args.out.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(args.pdf)
    try:
        if document.page_count != expected_pages:
            print(f"Page count mismatch: {document.page_count}", file=sys.stderr)
            return 2
        pages = (
            list(range(1, document.page_count + 1))
            if args.all_pages
            else list(args.pages or DEFAULT_PAGES)
        )
        if any(page < 1 or page > document.page_count for page in pages):
            print("Selected page is outside the document", file=sys.stderr)
            return 2
        chunks = pymupdf4llm.to_markdown(
            document,
            pages=[page - 1 for page in pages],
            page_chunks=True,
            use_ocr=False,
            force_ocr=False,
            show_progress=False,
        )
        by_page = {
            int((chunk.get("metadata") or {})["page_number"]): chunk
            for chunk in chunks
        }
        summary: dict[str, Any] = {
            "pdf": args.pdf.name,
            "sha256": actual_hash,
            "page_count": document.page_count,
            "pages": {},
        }
        for page_number in pages:
            before = str(by_page[page_number].get("text") or "")
            after, decisions, boundary_ok = repair_page_audited(
                document[page_number - 1], by_page[page_number]
            )
            before_path = args.out / f"page-{page_number:03d}.before.md"
            after_path = args.out / f"page-{page_number:03d}.after.md"
            diff_path = args.out / f"page-{page_number:03d}.diff"
            before_path.write_text(before)
            after_path.write_text(after)
            diff_path.write_text(
                "".join(
                    difflib.unified_diff(
                        before.splitlines(keepends=True),
                        after.splitlines(keepends=True),
                        fromfile=before_path.name,
                        tofile=after_path.name,
                    )
                )
            )
            summary["pages"][str(page_number)] = {
                "changed": before != after,
                "changes_confined_to_picture_text": boundary_ok,
                "decisions": decisions,
                "before_chars": len(before),
                "after_chars": len(after),
            }
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    finally:
        document.close()
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
