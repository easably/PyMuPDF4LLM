# OCR policy check, 16 October 2026

PDF: `cat-operator-manual-320-323.pdf`, SHA-256
`5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab`.

This is a quality comparison, not a production timing run. The timings below
were measured locally on the same pages and machine.

## Whole-file evidence

- Current PyMuPDF4LLM policy sent 413 of 530 pages to OCR even though those
  pages already had selectable text.
- The deployed text-only request took 424.945 seconds.
- A local full run with `use_ocr=False` took 31.9 seconds.
- Embedded-picture extraction was not the bottleneck: the deployed request
  with pictures took 425.541 seconds.

## Pages 101–110

Without OCR these ten pages produced 5,654 Markdown characters in 0.485
seconds. The current policy produced 13,345 characters in 6.000 seconds and
called OCR on pages 102–110.

The added characters were mostly noisy attempts to read lift-chart images.
For example, page 102 gained strings such as `a0 wom`, `Ea`, and fragmented
numbers. Page 109 gained long rows of broken letters, separators, and numbers.
The surrounding captions were already present in the text layer.

A simple low-text threshold does not distinguish these pages: pages 102–110
have 542–651 selectable characters each. A threshold below 500 skips all of
their chart OCR; a threshold of 700 keeps almost the same behavior as the
current policy.

## Other inspected pages

- Pages 8, 10, 21, 59, 60, and 61 have little selectable text. OCR added
  mostly symbols and broken diagram text, not useful prose.
- Page 26 is a counterexample: OCR recovered `CAT DEO-ULS` and `ACEA E9` from
  a label. The page already has 951 selectable characters, so a normal
  "low-text page" threshold would not select it.
- Page 419 already exposes the service-interval descriptions in its text
  layer. OCR added broken chart headers and symbols.
- Page 420 has 2,554 selectable characters. OCR changed reading order and
  merged unrelated columns, making the result worse.
- Page 528 has no selectable text, but it also produced no OCR text. It
  appears blank; it does not demonstrate scan recovery.

## Text-layer distribution

The 530 pages contain:

- 1 page with no selectable text;
- 11 pages with at most 200 characters;
- 38 pages with at most 500 characters;
- 173 pages with at most 700 characters;
- 309 pages with at least 1,000 characters.

## Conclusion

For this manual, OCR only on pages with no selectable text gives the best
speed/quality balance and matches the original service promise. It keeps OCR
for actual scan-only pages but intentionally does not try to read labels and
tables embedded as pictures on otherwise textual pages.

Using a low-text threshold is not supported by this sample: low-text pages
mostly gained OCR noise, while the one useful image label was on a page with
951 selectable characters. Recovering embedded-image text reliably needs a
separate image/region OCR policy, not a page character-count threshold.

## Decision

The selected policy is OCR only for pages with no selectable text. The service
now parses text pages with OCR disabled and scan-only pages with OCR enabled.
The `ocr_page` callback also refuses a non-empty text page as a safety check.

A full local service conversion with the selected policy took 34.054 seconds,
returned 529 non-empty pages, and produced 877,692 Markdown characters. This is
close to the earlier 31.9-second `use_ocr=False` probe; the machines differ
from the 424.945-second deployed run, so this is not a production speed claim.
