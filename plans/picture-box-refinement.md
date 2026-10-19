# Plan: shared picture box refinement and text repair prototype

Step 2 of `plans/problems/long-pdf-vector-drawings-dropped.md`. Needs the
corpus from `plans/acceptance-compact-track-loader.md`.

## Goal

1. A module that turns raw layout `picture` boxes into refined boxes.
2. A prototype that uses those boxes to restore text that layout wrongly
   put into `picture text`.

This plan delivers a prototype and a review. It does not change the
service response.

Status (19 October 2026): prototype approved after full dry-runs on the
220-page compact loader and 530-page CAT manuals. Integrated in service
version 0.4.0 behind `repair_picture_text`, off by default.

## Part A. Box refinement module

New module, for example `src/pymupdf4llm_service/picture_boxes.py`.
Pure functions; input is page data, output is boxes with reasons.

Inputs per page:

- layout `picture` boxes from the existing chunk (`page_boxes`);
- `page.get_drawings()`;
- text spans with boxes;
- embedded raster boxes.

Rules, each with a configurable threshold:

1. Mark frame paths (section or page frames). Exclude them from vector
   area measurement only. Never remove them from page data.
2. A candidate that almost matches an embedded raster box → `raster`.
3. No non-frame vector paths and no raster inside → reject, reason
   `text_only` (legend on 68, figure-ID line on 8).
4. Vector paths cover part of the box and text blocks lie outside them →
   shrink to vector area plus text overlapping it, reason `shrunk`
   (85, 181).
5. Too small after shrinking → reject, `too_small`.
6. Almost the whole frame after shrinking → reject, `frame_sized`.

Output per candidate: final box or none, reason, measured values.

Merging fragments and fallback clustering belong to step 3, not here.

Tests: generated PDFs with injected candidate boxes, one test per rule.
Layout itself is checked only through the corpus.

## Part B. Text repair prototype

Standalone script, for example `scripts/prototype_text_repair.py`. Not
wired into the service.

For each page:

1. Run the current `to_markdown(page_chunks=True)` call.
2. Run Part A on the page.
3. For each candidate with reason `text_only` or `shrunk`, build markdown
   from text spans that lie outside the final box, in reading order. Keep
   bullets and line breaks where the span data shows them. Read two-column
   regions column by column. Candidates with reason `raster` are left as
   they are: their text is not restored.
4. Replace the `picture text` block of that candidate with the restored
   text. Drop callout-only text of accepted pictures from markdown.
5. Write before and after markdown and a diff.

Run on pages 68, 85, 181, 205, and on at least ten normal text pages.

## Review

The result is a short analysis in
`acceptance/compact-track-loader/analyses/`:

- diffs for the reference pages;
- corpus text-phrase and legend metrics, before and after;
- regressions on normal text pages (expected: none);
- known failures: headings or lists that could not be restored.

Restoring headings and lists from coordinates is not guaranteed. The review
decides one of:

- integrate behind a setting, default off, as a separate text release;
- integrate a narrower version (for example only restore plain paragraphs);
- do not integrate yet.

If integrated, doc2graph re-indexes affected documents after the release.

## Done when

- Part A tests pass.
- The analysis file is written.
- A person has made the integration decision.

Decision: integrate without narrowing, behind a default-off setting. Enable
first on a doc2graph staging run; production enablement and re-indexing are
not part of this plan.
