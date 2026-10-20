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

## Review 2026-10-20 (2): commit a192787

Items 1–7 of the first review are done. Checked: indirect `/Length`
resolved, listed rasters only count as covered, mixed group measured with
all pictures (recall 100%), separate `VECTOR_DETECTION_TIMEOUT_SECONDS`,
`vector_pages_scanned` 220/220 and 530/530, thresholds and figure-ID
patterns in `VectorDrawingSettings` with `VECTOR_*` env names, `MAX_PICTURES`
kept as a total cap, CHANGELOG 0.5.0. 87 tests pass in a Linux Python 3.10
run.

Remaining items before deploy.

### 8. A small caution icon uses most of the raster budget

Raster xref 17 is the CAUTION triangle: 149×133 px, but drawn at about
17×15 pt. It occurs 43 times in the compact manual and passes the
100 px pixel-size filter each time. 43 of the 80 raster slots go to this
icon. The 81st raster occurrence is on page 56, so pages 56–220 return no
rasters at all. Three of the four false pictures in the mixed group are this
icon (pages 82, 97, 130).

- Add a displayed-size filter in PDF points, using `get_image_rects()`:
  skip occurrences whose shown box is smaller than
  `MIN_PICTURE_DISPLAY_SIDE_PT` (start 40) on either side. New reason
  `too_small_displayed`.
- Keep the pixel-size filter as it is.
- Report `raster_cap_reached_at_page` when the raster count cap stops
  listing, so a consumer sees where rasters end.
- Rerun both acceptances. Expected: no caution icons listed, rasters
  listed further into both manuals. Then decide whether the raster default
  of 80 is still too low for long manuals; record the decision in the run
  file.

### 9. Unclassified false pictures

The corpus run still shows 5 `unclassified` false pictures: pages 99, 100,
178, 184 (vector-only) and 130 (mixed). Look at each crop. Either:

- it is a real drawing missing from `labels.json` → add the label; or
- it is a detector error → add a `not_pictures` label with its type and
  fix the rule if the fix is small.

Report precision per group after this.

### 10. Memory headroom

Peak RSS on the CAT manual rises from 776 MiB (text) to 1070 MiB (full).
Check the container memory limit used in deployment. Document the
recommended memory in README next to the picture limits. If the limit is
close, lower `MAX_PICTURE_PNG_BYTES` or the store cap by default.

### 11. Optional: prefilter default

`prefilter_min_paths` defaults to 1, so almost every page runs full
detection. Speed is acceptable now (1.33× and 1.22×). Measure with 5 and
10 on both manuals; change the default only if recall on the corpus stays
the same.

### 12. Consumer check before deploy

- Run doc2graph indexing of the compact manual against 0.5.0 with
  `include_pictures=true` on a staging setup.
- Confirm doc2graph accepts the new `kind` and report fields without
  errors, and record how many pictures it captions.

Stop after item 10 (11 and 12 may follow) and report: tests, both
acceptance runs with per-group recall and precision, raster listed and
skipped by reason, `raster_cap_reached_at_page`, peak memory.

### Review 2 completion 2026-10-20

Items 8–10 done. Item 11 measured, default unchanged. Item 12 not run
(staging doc2graph).

- Tests: 91 passed locally.
- Compact corpus: vector-only and mixed both 100% recall and 100%
  precision; negatives still empty. False pictures: none.
- Compact full run: 80 rasters listed, 43 `too_small_displayed`, 169
  `count`, cap at page 58; 67 vectors; 220/220 pages; 1.31× text-only;
  750.7 MiB peak RSS.
- CAT full run: 80 rasters, 0 `too_small_displayed`, cap at page 111;
  4 vectors; 530/530 pages; 1.20× text-only. Deploy memory limit is 4 GiB;
  documented in README. Default PNG/store caps kept.
- Raster cap stays 80: both manuals still have hundreds of leftover
  rasters; raising the default is a caption-cost choice for doc2graph.
- Prefilter 5 and 10 keep 100% labeled recall; default remains 1.

## Review 2026-10-20 (3): commit efafc6b — ready for staging test

Checked: displayed-size filter, `raster_cap_reached_at_page`, fallback
leftovers absorbed into neighbouring pictures, memory documented, 91 tests
pass (Linux, Python 3.10). Nothing blocks a staging test. Item 12 is the
next step.

### 13. Staging test plan (doc2graph)

Run on the compact manual and the CAT manual, both against 0.5.0:

1. Container workflow green; record the image digest.
2. `include_pictures=true`, `repair_picture_text=false`: doc2graph accepts
   `kind` and the new report fields; no client errors.
3. Same with `repair_picture_text=true`: compare search answers before and
   after on fixed questions (H-pattern bucket dump, page 85; radio button 8,
   page 68; EMU indicator 14, page 181; hinge pin height, page 205).
4. Record caption calls per manual and total indexing time.

### 14. Raster cap is a consumer decision, measure it on staging

With the default `MAX_RASTER_PICTURES=80`, rasters stop at page 58 of 220
(compact) and page 111 of 530 (CAT). Later photos are not returned at all.

- On staging, run the compact manual twice: default 80 and a high cap
  (for example 300). Record caption calls, time, and whether answers about
  pages after 58 improve.
- Pick the doc2graph deploy value from that, not from the service default.

### 15. Holdout check against overfitting

Corpus precision went to 100% after rule changes and one label edit
(p184-dial) on the same corpus. Before calling the detector done:

- Label 10–15 drawing pages from a second manual that was not used for
  tuning (CAT, or another vendor without TX-style IDs).
- Run the acceptance script on it with current defaults, no tuning.
- Record recall and precision as the honest number in the README.

Items 13–14 are needed before production. Item 15 can run in parallel
with staging.

### Review 3 notes 2026-10-20

Item 15 started. CAT is a bad second *vector* corpus: almost no
vector-only drawings; path-heavy pages are tables; listed service vectors
on CAT were logos/headers (pages 1, 530) plus one table strip (page 500).

Holdout labels: `acceptance/cat-operator-manual/holdout-labels.json`
(11 pages). Run: `runs/2026-10-20-holdout.md`.

| Group | Recall | Precision | Notes |
| --- | --- | --- | --- |
| mixed | 100% | 90% | 9 raster drawings; 1 false table |
| negative | n/a | 0% | 3 false: 2 logos, 1 header banner |

No detector tuning on this holdout. Items 13–14 still need a staging
doc2graph run (compact + CAT, caps 80 vs 300). Optional follow-up: reject
logo/header-sized clusters so CAT pages 1 and 530 stay empty.

### Review 2026-10-20 (4): holdout result

The holdout is honest and useful, but it does not give an independent
vector recall number: CAT has almost no vector-only drawings. It confirms
raster recall and finds false vectors. Staging (items 13–14) is not
blocked.

16. Commit the holdout labels, run file, script change, and README text
    as they are.
17. Small false-positive fix, then rerun both corpora:
    - Reject vector candidates whose centre lies in a layout `page-header`
      or `page-footer` box, or in the top or bottom margin band of the page
      (setting, start 8% of page height), unless a figure ID or caption is
      next to them. Target: CAT pages 1 and 530 return no vector.
    - Check why page 500's table strip passed the fallback table check
      (`find_tables()` result, cluster size). Fix only if it is a
      threshold, not a new rule.
    - Compact corpus must stay at 100% recall; report precision on both.
18. Independent vector recall still open. Needs a third PDF with real
    vector drawings and no TX/TS IDs (another vendor's manual). When one is
    available: label 10–15 drawing pages, run with current defaults, record
    the number in README. Until then README states vector recall is
    measured on one manual only.

### Review 4 completion 2026-10-20

Item 16 committed (`3b9b587`). Item 17 done:

- Edge/header/footer rejection (`VECTOR_EDGE_BAND_SHARE`, default 0.08) unless
  figure id or caption nearby; small-area (< 4000 pt²) marks without evidence
  dropped (CAT page 530 logo).
- Page 500: not a threshold miss. After `import pymupdf.layout`, `find_tables()`
  returns empty on that page, so the existing table overlap rule never fires.
  Fix: do not resurrect clusters that sit inside a layout picture rejected as
  `text_only` / `too_small` / `frame_sized`. Layout `table` boxes were tried and
  rejected — they cover real compact drawings.
- Compact (`runs/2026-10-20-chrome-filter.md`): vector_only and mixed 100%
  recall and precision.
- CAT holdout (`runs/2026-10-20-holdout-chrome-filter.md`): mixed 100%/100%,
  negatives empty; pages 1, 500, 530 clean.
- 94 tests pass. Item 18 blocked until a third vendor PDF is available.
  Items 13–14 still need staging doc2graph.
