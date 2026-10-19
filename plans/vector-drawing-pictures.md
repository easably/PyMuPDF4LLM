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
