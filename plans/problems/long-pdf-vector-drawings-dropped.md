# Roadmap: drawings and text on long PDFs

Target repository: `PyMuPDF4LLM`. Recorded 16 October 2026, rewritten
19 October 2026.

This file is a map, not an implementation plan. Each step has its own plan.
An agent executes one plan at a time.

## Principle

The service is a low-level extractor. It calls no language model and no
vision model. It already uses one local model: `pymupdf-layout`, an ONNX
page-layout model on CPU, through `to_markdown`. No other model is added.

OCR runs only on scan-only pages, as today. Pages with a text layer use
their text and coordinates directly.

The service pre-extracts data. How doc2graph later describes drawings
(text only, or with a vision model) is decided in doc2graph.

## Findings (19 October 2026)

File: `tessdata/pdfs/compact track loader.pdf`, 220 pages.

- Each `to_markdown(page_chunks=True)` chunk already has `page_boxes`.
- Pages 85, 181: a whole section is a layout `picture`; its procedure text
  is flattened into `<!-- picture text -->`. Text bug in production.
- Page 68: the two-column legend is a `picture`; columns are merged.
- Page 205: drawing and spec table form one `table` box; drawing missed.
- Page 19: clean drawing crop from layout.
- Legacy `cluster_drawings` grows to the section frame on this manual.
- Layout put a `picture` box on 22 of 42 vector-only pages. Page
  coverage, not drawing recall.

## Steps

| # | Plan | Depends on | Ready for an agent |
|---|---|---|---|
| 1 | `plans/acceptance-compact-track-loader.md` — labeled corpus | — | yes |
| 2 | `plans/picture-box-refinement.md` — shared box refinement + text repair prototype | 1 | yes, prototype only |
| 3 | `plans/vector-drawing-pictures.md` — vector drawings as PNG | 1, 2 | after step 2 review |
| 4 | Figure context (future plan) | 3 | no |

Step 2 owns the box refinement module. Text repair and vector pictures both
use it. This removes the dependency of text repair on the vector plan.

## Step 4, not planned yet

Written after step 3 results. Candidate scope, to verify first:

- Figure ID and caption near a drawing.
- Callout numbers that are present in the text layer inside the drawing
  box, with their boxes. Callouts inside raster pictures are out of scope
  without OCR.
- Legend lines that start with a callout number, including "Continued on
  next page".
- Links from a picture to markdown blocks. Requires stable text block IDs,
  which the contract does not return today.

First task of step 4: measure on the corpus how many callouts really exist
as separate text spans with usable coordinates.

## Deferred

- Whole-page PNG renders. No fitting `kind`, high memory cost. Revisit only
  if doc2graph chooses page-level vision input; then as a separate
  `page_render` output, not in `pictures`.

## Out of scope

- Language or vision model calls in the service.
- OCR of text-layer pages.
- Docling fallback for long PDFs.
- A second PDF library.
