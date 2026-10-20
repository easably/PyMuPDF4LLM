#!/usr/bin/env python3
"""Measure layout picture candidates against the compact-loader corpus.

The PDF and this runner stay out of CI. Pure metric helpers are covered by
pytest; a real run checks the manual hash before importing the layout model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

DEFAULT_LABELS = ROOT / "acceptance" / "compact-track-loader" / "labels.json"
DEFAULT_PDF = ROOT / "tessdata" / "pdfs" / "compact track loader.pdf"

_PICTURE_TEXT = re.compile(
    r"<!-- Start of picture text -->(.*?)<!-- End of picture text -->",
    re.DOTALL,
)
_HTML_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_PUNCTUATION = re.compile(r"[*_~`|]+")
_CALLOUT_PREFIX = re.compile(r"(?:^|\s)\d{1,2}\s*[—-]\s*")

CandidateSource = Callable[
    [Any, dict[int, dict[str, Any]], list[int]], list[dict[str, Any]]
]


@dataclass(frozen=True)
class Match:
    drawing_id: str
    candidate_index: int
    iou: float


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bbox_iou(first: Iterable[float], second: Iterable[float]) -> float:
    ax0, ay0, ax1, ay1 = map(float, first)
    bx0, by0, bx1, by1 = map(float, second)
    width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    height = max(0.0, min(ay1, by1) - max(ay0, by0))
    intersection = width * height
    first_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    second_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def greedy_matches(
    drawings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    threshold: float,
) -> list[Match]:
    """Match boxes one-to-one, taking the highest IoU pair first."""
    pairs: list[tuple[float, str, int]] = []
    for drawing in drawings:
        for index, candidate in enumerate(candidates):
            overlap = bbox_iou(drawing["bbox"], candidate["bbox"])
            if overlap >= threshold:
                pairs.append((overlap, str(drawing["id"]), index))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_drawings: set[str] = set()
    used_candidates: set[int] = set()
    matches: list[Match] = []
    for overlap, drawing_id, candidate_index in pairs:
        if drawing_id in used_drawings or candidate_index in used_candidates:
            continue
        used_drawings.add(drawing_id)
        used_candidates.add(candidate_index)
        matches.append(Match(drawing_id, candidate_index, overlap))
    return matches


def false_picture_type(
    candidate: dict[str, Any],
    not_pictures: list[dict[str, Any]],
    minimum_iou: float = 0.5,
) -> str:
    ranked = sorted(
        (
            (bbox_iou(candidate["bbox"], item["bbox"]), str(item["type"]))
            for item in not_pictures
        ),
        reverse=True,
    )
    if ranked and ranked[0][0] >= minimum_iou:
        return ranked[0][1]
    return "unclassified"


def raw_layout_candidates(
    _document: Any,
    chunks_by_page: dict[int, dict[str, Any]],
    pages: list[int],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for page_number in pages:
        chunk = chunks_by_page[page_number]
        for index, box in enumerate(chunk.get("page_boxes") or []):
            if box.get("class") != "picture":
                continue
            candidates.append(
                {
                    "id": f"layout-p{page_number:03d}-{index}",
                    "page": page_number,
                    "bbox": [float(value) for value in box["bbox"]],
                    "detector": "raw_layout",
                }
            )
    return candidates


def load_candidate_source(spec: str) -> CandidateSource:
    if spec == "raw-layout":
        return raw_layout_candidates
    if ":" not in spec:
        raise ValueError("candidate source must be raw-layout or module:function")
    module_name, function_name = spec.split(":", 1)
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"candidate source {spec} is not callable")
    return function


def normalize_text(value: str) -> str:
    value = _HTML_TAG.sub(" ", value)
    value = _MARKDOWN_PUNCTUATION.sub("", value)
    return " ".join(value.split())


def split_picture_text(markdown: str) -> tuple[str, str]:
    blocks = _PICTURE_TEXT.findall(markdown)
    normal = _PICTURE_TEXT.sub(" ", markdown)
    return normal, "\n".join(blocks)


def phrase_location(markdown: str, phrase: str) -> str:
    normal, picture = split_picture_text(markdown)
    needle = normalize_text(phrase)
    if needle in normalize_text(normal):
        return "normal"
    if needle in normalize_text(picture):
        return "picture_text"
    return "missing"


def logical_lines(markdown: str) -> list[str]:
    expanded = markdown.replace("<br>", "\n").replace("<br/>", "\n")
    return [normalize_text(line) for line in expanded.splitlines() if normalize_text(line)]


def legend_line_status(markdown: str, expected: str) -> str:
    needle = normalize_text(expected)
    containing = [line for line in logical_lines(markdown) if needle in line]
    if not containing:
        return "missing"
    if any(len(_CALLOUT_PREFIX.findall(line)) == 1 for line in containing):
        normal, _picture = split_picture_text(markdown)
        if any(needle in line for line in logical_lines(normal)):
            return "normal_separate"
        return "picture_separate"
    return "merged"


class PeakRss:
    """Sample this process' resident memory while native layout code runs."""

    def __init__(self, interval_seconds: float = 0.02) -> None:
        self.interval_seconds = interval_seconds
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "PeakRss":
        import psutil

        process = psutil.Process()

        def sample() -> None:
            while not self._stop.is_set():
                self.peak_bytes = max(self.peak_bytes, int(process.memory_info().rss))
                self._stop.wait(self.interval_seconds)
            self.peak_bytes = max(self.peak_bytes, int(process.memory_info().rss))

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


def validate_labels(labels: dict[str, Any]) -> None:
    pages = labels.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("labels.pages must be a non-empty list")
    page_numbers = [int(item["page"]) for item in pages]
    if len(page_numbers) != len(set(page_numbers)):
        raise ValueError("each page may occur only once in labels.json")
    drawing_ids: list[str] = []
    for page in pages:
        if page["group"] not in {"vector_only", "mixed", "negative"}:
            raise ValueError(f"unknown page group on page {page['page']}")
        for collection in ("drawings", "not_pictures"):
            for item in page.get(collection, []):
                x0, y0, x1, y1 = map(float, item["bbox"])
                if x1 <= x0 or y1 <= y0:
                    raise ValueError(f"invalid {collection} bbox on page {page['page']}")
        drawing_ids.extend(str(item["id"]) for item in page.get("drawings", []))
    if len(drawing_ids) != len(set(drawing_ids)):
        raise ValueError("drawing ids must be unique")


def evaluate(
    labels: dict[str, Any],
    chunks_by_page: dict[int, dict[str, Any]],
    candidates: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    by_page_candidate: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_page_candidate.setdefault(int(candidate["page"]), []).append(candidate)

    group_totals: dict[str, Counter[str]] = {}
    false_types: Counter[str] = Counter()
    page_results: list[dict[str, Any]] = []
    text_results: list[dict[str, Any]] = []
    legend_results: list[dict[str, Any]] = []

    for page_label in labels["pages"]:
        page_number = int(page_label["page"])
        group = str(page_label["group"])
        drawings = list(page_label.get("drawings", []))
        page_candidates = by_page_candidate.get(page_number, [])
        matches = greedy_matches(drawings, page_candidates, threshold)
        matched_candidates = {match.candidate_index for match in matches}
        unmatched = [
            candidate
            for index, candidate in enumerate(page_candidates)
            if index not in matched_candidates
        ]
        page_false_types = Counter(
            false_picture_type(candidate, page_label.get("not_pictures", []))
            for candidate in unmatched
        )
        false_types.update(page_false_types)

        totals = group_totals.setdefault(group, Counter())
        totals["drawings"] += len(drawings)
        totals["candidates"] += len(page_candidates)
        totals["matches"] += len(matches)
        totals["false"] += len(unmatched)

        markdown = str(chunks_by_page[page_number].get("text") or "")
        for phrase in page_label.get("text_phrases", []):
            text_results.append(
                {
                    "page": page_number,
                    "phrase": phrase,
                    "location": phrase_location(markdown, phrase),
                }
            )
        for line in page_label.get("legend_lines", []):
            legend_results.append(
                {
                    "page": page_number,
                    "line": line,
                    "status": legend_line_status(markdown, line),
                }
            )

        page_results.append(
            {
                "page": page_number,
                "group": group,
                "drawings": len(drawings),
                "candidates": len(page_candidates),
                "matches": len(matches),
                "false": len(unmatched),
                "false_types": dict(sorted(page_false_types.items())),
            }
        )

    groups: dict[str, dict[str, Any]] = {}
    for group, totals in sorted(group_totals.items()):
        drawings = totals["drawings"]
        candidates_count = totals["candidates"]
        matches_count = totals["matches"]
        groups[group] = {
            **dict(totals),
            "recall": matches_count / drawings if drawings else None,
            "precision": matches_count / candidates_count if candidates_count else None,
        }

    return {
        "groups": groups,
        "false_types": dict(sorted(false_types.items())),
        "pages": page_results,
        "text_phrases": text_results,
        "legend_lines": legend_results,
    }


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_report(
    *,
    pdf_path: Path,
    sha256: str,
    candidate_source: str,
    mixed_candidate_source: str | None,
    threshold: float,
    elapsed_seconds: float,
    peak_rss_bytes: int,
    result: dict[str, Any],
) -> str:
    lines = [
        "# Compact track loader baseline",
        "",
        f"- PDF: `{pdf_path}`",
        f"- SHA-256: `{sha256}`",
        f"- Candidate source: `{candidate_source}`",
        (
            f"- Mixed-page candidate source: `{mixed_candidate_source}`"
            if mixed_candidate_source
            else "- Mixed-page candidate source: same as candidate source"
        ),
        f"- IoU threshold: `{threshold}`",
        f"- Layout time: `{elapsed_seconds:.2f} s`",
        f"- Peak process RSS: `{peak_rss_bytes / 1024 / 1024:.1f} MiB`",
        "",
        "## Drawing metrics",
        "",
        "| Group | Drawings | Candidates | Matches | False | Recall | Precision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group, values in result["groups"].items():
        lines.append(
            f"| {group} | {values['drawings']} | {values['candidates']} | "
            f"{values['matches']} | {values['false']} | "
            f"{_ratio(values['recall'])} | {_ratio(values['precision'])} |"
        )

    lines.extend(["", "## False pictures", ""])
    if result["false_types"]:
        for kind, count in result["false_types"].items():
            lines.append(f"- `{kind}`: {count}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Per-page boxes",
            "",
            "| Page | Group | Drawings | Candidates | Matches | False | Types |",
            "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for page in result["pages"]:
        kinds = ", ".join(
            f"{kind}:{count}" for kind, count in page["false_types"].items()
        )
        lines.append(
            f"| {page['page']} | {page['group']} | {page['drawings']} | "
            f"{page['candidates']} | {page['matches']} | {page['false']} | "
            f"{kinds or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Text phrases",
            "",
            "| Page | Location | Phrase |",
            "| ---: | --- | --- |",
        ]
    )
    for item in result["text_phrases"]:
        phrase = str(item["phrase"]).replace("|", "\\|")
        lines.append(f"| {item['page']} | {item['location']} | {phrase} |")

    legend_counts = Counter(item["status"] for item in result["legend_lines"])
    lines.extend(["", "## Legend integrity", ""])
    for status, count in sorted(legend_counts.items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(
        [
            "",
            "| Page | Status | Expected line |",
            "| ---: | --- | --- |",
        ]
    )
    for item in result["legend_lines"]:
        expected = str(item["line"]).replace("|", "\\|")
        lines.append(f"| {item['page']} | {item['status']} | {expected} |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--candidate-source", default="raw-layout")
    parser.add_argument("--mixed-candidate-source")
    parser.add_argument("--iou-threshold", type=float)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    labels = json.loads(args.labels.read_text())
    validate_labels(labels)

    expected = labels["pdf"]
    actual_sha256 = file_sha256(args.pdf)
    if actual_sha256 != expected["sha256"]:
        print(
            f"SHA-256 mismatch: expected {expected['sha256']}, got {actual_sha256}",
            file=sys.stderr,
        )
        return 2

    # Delay heavy imports until the corpus identity has been checked.
    import pymupdf
    import pymupdf4llm

    document = pymupdf.open(args.pdf)
    try:
        if document.page_count != int(expected["page_count"]):
            print(
                f"Page count mismatch: expected {expected['page_count']}, "
                f"got {document.page_count}",
                file=sys.stderr,
            )
            return 2
        started = time.perf_counter()
        with PeakRss() as memory:
            chunks = pymupdf4llm.to_markdown(
                document,
                page_chunks=True,
                use_ocr=False,
                force_ocr=False,
                show_progress=False,
            )
        elapsed = time.perf_counter() - started
        if not isinstance(chunks, list):
            raise RuntimeError("PyMuPDF4LLM did not return page chunks")
        chunks_by_page = {
            int((chunk.get("metadata") or {})["page_number"]): chunk
            for chunk in chunks
        }
        selected_pages = [int(item["page"]) for item in labels["pages"]]
        missing = sorted(set(selected_pages) - set(chunks_by_page))
        if missing:
            raise RuntimeError(f"missing chunks for labeled pages: {missing}")
        source = load_candidate_source(args.candidate_source)
        candidates = source(document, chunks_by_page, selected_pages)
        if args.mixed_candidate_source:
            mixed_pages = [
                int(item["page"])
                for item in labels["pages"]
                if item["group"] == "mixed"
            ]
            mixed_source = load_candidate_source(args.mixed_candidate_source)
            mixed_candidates = mixed_source(document, chunks_by_page, mixed_pages)
            mixed_page_set = set(mixed_pages)
            candidates = [
                candidate
                for candidate in candidates
                if int(candidate["page"]) not in mixed_page_set
            ] + mixed_candidates
    finally:
        document.close()

    threshold = (
        float(args.iou_threshold)
        if args.iou_threshold is not None
        else float(labels["matching"]["iou_threshold"])
    )
    result = evaluate(labels, chunks_by_page, candidates, threshold)
    report = render_report(
        pdf_path=args.pdf,
        sha256=actual_sha256,
        candidate_source=args.candidate_source,
        mixed_candidate_source=args.mixed_candidate_source,
        threshold=threshold,
        elapsed_seconds=elapsed,
        peak_rss_bytes=memory.peak_bytes,
        result=result,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report)
        print(args.report)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
