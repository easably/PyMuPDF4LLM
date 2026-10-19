# Compact track loader acceptance corpus

Manual acceptance corpus for vector drawings and layout text in
`compact track loader.pdf`.

Expected file:

- SHA-256 `ef29c72b6659a61f2d7905ffcbe7ec03375e45ae37c84a801a44f3940edcdf14`
- 220 pages

The PDF stays in `tessdata/pdfs/` and is ignored by git. The runner checks
its hash and page count before parsing it.

## Selection

The starting inventory selected all 42 pages that had no embedded raster
at least 100 pixels wide and high, and at least 30 drawing paths. Manual
review then separated real drawings from forms, tables, rules, and text.

The corpus also includes:

- mixed raster/vector pages 3, 30, 70, 82, 97, and 130;
- ordinary text pages 2, 10, and 15–18;
- reference pages 8, 13, 19, 68, 85, 135, 181, and 205.

`labels.json` records approximate boxes in PDF points. A candidate and a
drawing are matched one-to-one, taking the highest IoU (box overlap) first.
The default threshold is 0.5. An unmatched candidate, including a duplicate
candidate for an already matched drawing, is a false picture.

One printed figure ID (`TX…—UN—…`) means one drawing. Two adjacent regions
with different figure IDs or different captions are labeled separately and
must not be merged. A repeated horizontal and vertical rendering of the same
ID around one figure is still one ID and one drawing.

A drawing box contains the graphics and callout numbers only. Its caption,
legend, and figure ID stay outside the box so they remain normal text and can
be extracted into separate metadata later.

The reference-page labels require human review before the corpus is used as
a release gate. Pages 82–85 split the cockpit view and the operation pattern
because they have separate IDs. Page 181 likewise contains two separately
identified EMU diagrams.

## Run

The baseline uses raw `pymupdf-layout` picture boxes:

```bash
.venv/bin/python scripts/accept_compact_track_loader.py \
  --pdf "tessdata/pdfs/compact track loader.pdf" \
  --report acceptance/compact-track-loader/runs/2026-10-19-local.md
```

Use `--candidate-source module:function` for a later detector. The callable
receives the open document, chunks keyed by 1-based page number, and selected
page numbers, and returns dictionaries containing `page` and `bbox`.

Runs report drawing recall and precision by group, false-picture categories,
text phrases found in normal Markdown versus `picture text`, legend-line
integrity, elapsed time, and peak process memory.

Known text-repair limitation: on multi-column procedures such as page 181,
the right-hand `YES`/`NO` answers are restored after the main column instead
of being paired row by row with their `LOOK` questions.

This is manual acceptance. Pytest checks the runner's metric functions but
does not open the 10 MiB manual.

