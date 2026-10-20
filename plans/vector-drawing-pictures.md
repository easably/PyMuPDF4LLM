# Plan: return vector drawings as pictures

Step 3 of `plans/problems/long-pdf-vector-drawings-dropped.md`. Starts
after the review of `plans/picture-box-refinement.md`. Uses its box
refinement module and the corpus.

## Goal

A downloadable PNG for each vector drawing, through the existing picture
IDs. No whole-page renders. No figure context (step 4).

## Contract

- Keep `include_pictures=true`, the `pictures` list, and the download
  endpoint.
- Add `kind`: `embedded_raster` or `vector_drawing`.
- Keep `id`, `page`, `index_on_page`, `width`, `height`. One
  `index_on_page` sequence for both kinds, sorted top-to-bottom,
  left-to-right.
- Report: keep top-level `listed`, `skipped`, `truncated`, `reasons` as
  totals. Add counts by kind, and for vectors by detector (`layout`,
  `fallback`). Change the scope text so it no longer says only rasters.
- Do not return PDF text, drawing commands, or coordinates from the
  download endpoint.

## Detection

1. Refined boxes from the step 2 module.
2. Split a candidate that contains multiple figure IDs or multiple
   captions into one candidate per identified figure. Pages 82–85 each
   contain separately identified cockpit and operation-pattern drawings.
3. Merge nearby accepted boxes only when they are fragments of the same
   figure. Never merge across different figure IDs (`TX…—UN—…`) or
   different captions. Page 8 is two figures, CE and EAC; pieces within
   either sign may merge, but the two signs must stay separate.
4. Fallback: when much non-frame vector content is not covered by any
   accepted box or raster, run `page.cluster_drawings()` on the uncovered
   paths.
   - Frame paths are excluded from clustering input only.
   - Reject clusters that substantially overlap a ruled table from
     `page.find_tables()`. Do not trust the layout `table` class (page 205).
   - Reject too small, too few paths, or frame-sized clusters.
5. Section frames are a hint, not a rule. A candidate that crosses a frame
   border gets a lower score or is split at the border only when the parts
   on each side contain vector content. It is never rejected for crossing
   alone.
6. Render: small margin, clip to page,
   `page.get_pixmap(clip=box, dpi=150, alpha=False)`, store in
   `PictureStore`.

All thresholds are settings with start values from the corpus.

## Optional signal

`may_have_unextracted_graphics` per page: true when non-frame vector
content above a threshold remains uncovered after detection. Ship it only
if its precision on the corpus negatives (tables, forms, decorative rules)
is acceptable. Otherwise leave it out.

## Limits

- Separate configurable caps for raster and vector counts.
- One shared budget for extraction time, decoded PNG bytes, and store bytes.
- Set the vector cap from measured counts after refinement, not from the
  450 raw layout boxes.
- When a limit stops work: keep text and stored pictures, mark truncated,
  give a reason. Add a reason for render failure.
- Time is checked between pictures; it does not interrupt `get_pixmap()`.
- Failed and cancelled conversions release raster and vector IDs.

## Tests

Generated PDFs with injected candidate boxes:

- drawing with label → one `vector_drawing` PNG containing the label;
- embedded raster unchanged;
- raster and drawing on one page → both, stable order;
- raster inside a candidate → no vector duplicate;
- one candidate with two figure IDs → two pictures;
- fragments merged;
- adjacent drawings with different figure IDs or captions stay separate;
- framed section → drawing found, frame not; dimension lines kept;
- uncovered drawing → found by fallback;
- ruled table → not returned;
- tiny line ignored;
- drawing at page edge clipped safely;
- count, time, decoded-size, store, render-failure limits report losses;
- failed and cancelled conversions release IDs;
- `include_pictures=false` → no detection, no render;
- repeated fetch and ID expiration unchanged.

Keep the current vector test, with the new expectation
`kind=vector_drawing` and raster count zero.

## Acceptance

On the corpus:

- Drawing recall and precision per group (vector-only, mixed, negatives)
  are measured. Start target: recall ≥ 80% on vector-only, no regression on
  mixed pages, no pictures on negatives. Confirm targets after the first
  run.
- Pages 19, 68 (diagram only), 85, 181, 205 return the expected drawings.
- Conversion with pictures stays within 2× of text-only time; record peak
  memory.

Also:

- CAT manual acceptance shows no raster regression.
- doc2graph indexes the PNG through its current picture path.
- README documents the picture contract and known limits.
- Service version bumped; image passes the Python 3.12 Container workflow
  and is published with a known digest.

## doc2graph follow-up

- Stop calling every picture an embedded raster.
- Show raster and vector counts in the journal.

## Review 2026-10-20: fixes before commit

Review of the uncommitted 0.5.0 changes. Detection quality on labeled pages
is good (vector-only recall 97.6%, precision 90.9%). The items below block
the commit. Do them in order and stop for review after item 7.

### 1. Embedded rasters with an indirect `/Length` are dropped (existing bug)

`_stream_length()` returns `None` when `/Length` is an indirect reference
(`('xref', '70 0 R')`). Every raster in the compact track loader manual is
then skipped as `encoded_size`: 0 listed, 292 skipped. The vector detector
still treats those raster areas as covered, so the figure is lost
completely (pages 3, 30, 70, 97, 130).

- Resolve the indirect object and read its integer value.
- If the length still cannot be read, fall back to a bounded measure (for
  example `len(document.xref_stream_raw(xref))` only when the image pixel
  size is under the decode budget). Do not skip silently.
- Do not treat a raster box as covered for vector detection unless that
  raster was actually listed. Otherwise a skipped raster hides a drawing.
- Test: generated PDF with an indirect `/Length` image → one
  `embedded_raster`.
- Rerun both acceptances; the CAT raster byte comparison must still pass.

### 2. The acceptance mixed group measures vectors only

`vector_candidates()` returns only vector boxes, so raster drawings in the
mixed group can never match. The 60% mixed recall mixes a real loss (item 1)
with a measurement gap.

- Add a candidate source that returns all listed pictures (raster boxes
  from `get_image_rects`, plus vector boxes), and report mixed recall from
  it. Keep the vector-only source for the vector-only group.

### 3. Shared time budget starves vector detection on long manuals

With the default 20 s budget the vector pass stops early:

- CAT manual (530 pages): 2 vector drawings listed. The raster loop uses
  most of the budget, including time spent on rasters skipped by count.
- Compact manual on a slower machine: vector scan stopped at page 130 of
  220.

A long manual is the main use case, so this must work by default.

- Give vector detection its own time budget
  (`VECTOR_DETECTION_TIMEOUT_SECONDS`), separate from raster extraction.
  Keep the shared decoded-byte and store budgets.
- Stop counting rasters after the raster count cap is reached; skip them
  without decoding work beyond what is needed for the report.
- Add a cheap page prefilter before `detect_vector_drawings()`: skip pages
  with no layout `picture` box and fewer than N non-frame paths. Call
  `page.find_tables()` only when a fallback cluster needs the table check.
- Record per-page detection time in the acceptance run; list the 5 slowest
  pages.
- Target: the full CAT manual and the full compact manual finish vector
  detection without a `time` stop on the reference machine.

### 4. Truncation is under-reported

When the deadline hits at page level the loop adds 1 to `skipped` and
breaks. The report then says "1 skipped" although the rest of the document
was never scanned.

- Add `vector_pages_scanned` and `vector_pages_total` to the report.
- `truncated` must be true whenever `vector_pages_scanned <
  vector_pages_total`.
- Test: tiny time budget on a multi-page PDF → pages scanned < total,
  truncated true.

### 5. Hard-coded thresholds

The plan says all thresholds are settings. These are still literals in
`vector_drawings.py`: merge gap `85.0`, overlap ratios `0.35`, figure-ID
search margins `100` and `150`, fallback table rule `0.35`, `0.08`, `0.2`,
split offsets `4.0`, `8.0`, `16.0`.

- Move them into `VectorDrawingSettings` with comments.
- Allow overriding the settings through environment variables or one
  settings object passed from `pictures.py`; document the names.

### 6. Figure-ID pattern is manual-specific

`_FIGURE_ID = (?:TX|TS)\d{5,}` matches this manual's IDs only. On other
documents split and merge rules silently fall back to geometry.

- Make the pattern a setting (list of regexes), default to the current one.
- Document in README that figure-ID rules are optional hints and what
  happens when no ID is found.
- Report in the CAT run how many vector drawings were split or merged by ID
  (expected near zero) so the fallback behaviour is visible.

### 7. Picture count change for consumers

Before 0.5.0 one convert returned at most 80 pictures. Now it can return 80
rasters plus 120 vectors. doc2graph captions every picture.

- State the new maximum and the env names in README and in the changelog
  of 0.5.0.
- Keep `MAX_PICTURES` working as a total cap if it is set, so an operator
  can keep the old limit.

Stop here for review. Report: test count, both acceptance runs with
per-group recall and precision (mixed from item 2), vector pages scanned vs
total for both manuals, total time ratio, peak memory.

### Review completion 2026-10-20

All seven items were implemented.

- Tests: 87 passed before the final release verification.
- Compact corpus: vector-only recall 97.6%, precision 90.9%; mixed
  raster/vector recall 100.0%, precision 78.9%; negatives returned no
  candidates.
- Compact full run: 220/220 vector pages scanned, 1.33x text-only time,
  866.9 MiB peak RSS.
- CAT full run: 530/530 vector pages scanned, 1.22x text-only time,
  1070.1 MiB peak RSS; 80 rasters and 4 vectors downloaded with no failed
  GET or raster-byte mismatch.
- Neither run stopped vector detection on time.
