# Plan: labeled acceptance corpus, compact track loader manual

Step 1 of `plans/problems/long-pdf-vector-drawings-dropped.md`.

## Goal

A hand-labeled corpus and a measuring script. Steps 2 and 3 are accepted
against it. No service code changes in this plan.

## Location

`acceptance/compact-track-loader/`, same structure as
`acceptance/cat-operator-manual/`:

- `README.md`: file name, SHA-256, page count (220), how to run.
- `labels.json`: the labels below.
- `runs/`: one file per run, date and host.
- `analyses/`.

The PDF stays in `tessdata/pdfs/` and out of git. The script checks the
SHA-256 before anything else.

## Page selection

Three groups, so the corpus is not biased to vector-only pages:

1. **Vector-only**: no embedded raster ≥ 100 px, many drawing paths.
   Candidates with ≥ 150 paths: 19, 38, 39, 53, 68, 83, 84, 85, 99, 100,
   108, 136, 145, 177, 178, 181, 205. Extend with the lower-threshold
   pages from the 19 October inventory (42 total).
   Page 135 has many paths but is a table: it is a negative, not a
   drawing page.
2. **Mixed raster + vector**: candidates 3, 30, 70, 82, 97, 130. Add more if
   the inventory shows them.
3. **Negatives**: forms, tables, text-only pages with frames and rules
   (13, 135, plus at least five normal text pages).

Record the selection rule in the README, not only the page list.

## Labels per page

- `drawings`: each drawing with an approximate box (PDF points) and
  `source`: `vector`, `raster`, or `mixed`.
- `not_pictures`: boxes that must not be returned: tables, forms, legends,
  frames, figure-ID lines.
- `text_phrases`: phrases that must appear as normal markdown text, not in
  `picture text` (for example `Pivot joystick left` on page 85).
- `legend_lines`: on pages 68 and 205, legend lines that must stay separate.

One figure ID (`TX…—UN—…`) defines one drawing. Regions with different
figure IDs or different captions are labeled separately, even when they
share a column. Repeated horizontal and vertical renderings of the same ID
around one figure do not create a second drawing.

A drawing box contains graphics and callout numbers. Captions, legends, and
figure IDs stay outside it.

Matching is one-to-one. A returned box can match at most one labeled
drawing, and a labeled drawing at most one returned box, when IoU ≥ a
configurable threshold (start 0.5). Pair by highest IoU first. Unmatched
returned boxes, including duplicates of an already matched drawing, count
as false pictures. Record the threshold in each run.

## Script

`scripts/accept_compact_track_loader.py`, Python API only, no HTTP needed:

- Runs the current `to_markdown(page_chunks=True)` call as the service does.
- Reports per group:
  - drawing recall and precision for any given set of candidate boxes;
  - false pictures by type;
  - text phrase hits as normal text vs inside `picture text`;
  - legend line integrity;
  - time and peak memory.
- Takes candidate boxes from a pluggable source: raw layout `picture`
  boxes now, refined boxes in step 2, final boxes in step 3.

## Done when

- `labels.json` covers all three groups and the reference pages 8, 13, 19,
  68, 85, 135, 181, 205.
- A baseline run with raw layout boxes is recorded in `runs/`.
- A person has reviewed the labels of the reference pages.
