# Drawing acceptance — cat-operator-manual-320-323.pdf (after tuning)

After the chrome/logo/`text_only` filter. Not an untuned holdout: pages 1, 500,
and 530 were fixed on this manual. Untuned numbers:
`runs/2026-10-20-holdout.md` (mixed precision 90%).

- PDF: `tessdata/pdfs/cat-operator-manual-320-323.pdf`
- SHA-256: `5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab`
- Candidate source: `pymupdf4llm_service.vector_drawings:vector_candidates`
- Mixed-page candidate source: `pymupdf4llm_service.vector_drawings:all_picture_candidates`
- IoU threshold: `0.5`
- Layout time: `33.15 s`
- Peak process RSS: `669.8 MiB`

## Drawing metrics

| Group | Drawings | Candidates | Matches | False | Recall | Precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mixed | 9 | 9 | 9 | 0 | 100.0% | 100.0% |
| negative | 0 | 0 | 0 | 0 | n/a | n/a |

## False pictures

- None

## Per-page boxes

| Page | Group | Drawings | Candidates | Matches | False | Types |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | negative | 0 | 0 | 0 | 0 | — |
| 2 | negative | 0 | 0 | 0 | 0 | — |
| 24 | mixed | 4 | 4 | 4 | 0 | — |
| 26 | mixed | 2 | 2 | 2 | 0 | — |
| 42 | negative | 0 | 0 | 0 | 0 | — |
| 62 | negative | 0 | 0 | 0 | 0 | — |
| 74 | negative | 0 | 0 | 0 | 0 | — |
| 80 | negative | 0 | 0 | 0 | 0 | — |
| 330 | mixed | 1 | 1 | 1 | 0 | — |
| 500 | mixed | 2 | 2 | 2 | 0 | — |
| 530 | negative | 0 | 0 | 0 | 0 | — |

## Text phrases

| Page | Location | Phrase |
| ---: | --- | --- |

## Legend integrity


| Page | Status | Expected line |
| ---: | --- | --- |
