# Text repair prototype review

PDF: `tessdata/pdfs/compact track loader.pdf`  
SHA-256: `ef29c72b6659a61f2d7905ffcbe7ec03375e45ae37c84a801a44f3940edcdf14`

The standalone prototype was run on pages 68, 85, 181, and 205, plus ten
normal-text samples (2, 4, 9, 10, 13, 15, 16, 17, 18, and 135). Generated
Markdown and unified diffs are under `.tmp/compact-labels/text-repair/`.
These artifacts were reviewed before the opt-in service integration.

## Full-document dry-run

The final prototype was also run over every page of both known manuals:

- Compact track loader: 220 pages, 99 pages changed.
- CAT operator manual: 530 pages, 8 pages changed.
- Automatic boundary check: zero failures in 750 pages. Every unchanged
  byte outside the former `picture text` source ranges remained identical.

Fifteen changed pages were reviewed across the two manuals, including compact
pages 3, 68, 74, 85, 129, 181, and 193, and CAT pages 100, 288, 293, 308,
331, 458, 500, and 506. The review found two issues and both were corrected:

- empty layout picture boxes no longer produce whitespace-only changes;
- a layout box containing a substantial embedded raster is now classified
  as `raster` and left untouched, even when the box also covers vector paths.

The complete summaries and diffs are under `.tmp/text-repair-full/`.

## Corpus text metrics

Across all text annotations in `labels.json`:

- Before: 13 phrases in normal Markdown and 8 in `picture text`.
- After: 20 phrases in normal Markdown and 1 in `picture text`.
- The remaining phrase is on page 193, which was outside this prototype's
  reference-page set.

Across all legend annotations:

- Before: 10 normal separate lines, 6 separate `picture text` lines, 9 merged
  lines, and 6 missing lines.
- After: all 31 expected lines are separate normal Markdown lines.
- Legend lines no longer inherit bold formatting from their PDF spans.

All ten normal-text sample pages were byte-for-byte unchanged.

## Reference pages

### Page 68

The two interleaved legend columns are emitted column by column. Wrapped
entries such as `8— Audio and Menu Adjustment Button` and
`11— Increase Manual Tune Frequency Button` are reconstructed as single
lines. The 21 diagram callout numbers are not duplicated in Markdown.

### Page 85

The flattened block becomes a heading, a paragraph, three bullets, and
separate figure IDs and captions. For example:

```text
The right joystick (1) controls the bucket:
- Pivot joystick left to roll back bucket.
- Pivot joystick right to dump bucket.
```

### Page 181

The procedure and both legends return to normal Markdown. All four annotated
phrases move from `picture text` to normal text. Reading order is improved,
but the right-hand result column follows the main column rather than being
paired with each corresponding `LOOK` instruction.

### Page 205

Unchanged. Layout classifies the drawing and dimensions as a `table`, so
there is no `picture text` candidate for this module to repair.

## Known limitations

- Bold and italic span metadata can over-format legends and procedure text.
- Wrapped procedure paragraphs remain separate visual lines; the prototype
  does not infer paragraph continuation.
- Multi-column procedure flow on page 181 is column-correct but not
  semantically paired row by row.
- Page 193 still has a phrase in `picture text`; it should be added to a
  broader validation set before integration.
- This prototype does not split one accepted picture box into multiple
  drawings. That remains step 3.

## Review decision

Approved for integration without narrowing. Service version 0.4.0 exposes
the behavior through `repair_picture_text`, which remains off by default.
Enablement on doc2graph and re-indexing are separate follow-up steps.
