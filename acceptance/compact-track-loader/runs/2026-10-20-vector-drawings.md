# Vector drawing acceptance — 2026-10-20

- PDF SHA-256: `ef29c72b6659a61f2d7905ffcbe7ec03375e45ae37c84a801a44f3940edcdf14`
- IoU threshold: `0.5`
- Layout time: `19.30 s`
- Peak process RSS during the labeled-page run: `652.2 MiB`

## Detection

| Group | Drawings | Candidates | Matches | False | Recall | Precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| vector-only | 41 | 44 | 40 | 4 | 97.6% | 90.9% |
| mixed (raster + vector) | 15 | 19 | 15 | 4 | 100.0% | 78.9% |
| negatives | 0 | 0 | 0 | 0 | n/a | n/a |

Pages 19, 68, 85, 181, and 205 returned their expected drawings. Pages
82–85 kept one PNG per figure ID. The negative pages returned no candidates.

## Full conversion

- Text only: `22.53 s`
- Text plus pictures: `29.89 s`
- Ratio: `1.33x`
- Peak process RSS: text `784.7 MiB`; full `866.9 MiB`
- Embedded rasters: `80` listed, `212` skipped by the raster count cap
- Vector PNGs: `71` listed (`53` layout, `18` fallback), `0` skipped
- Vector pages scanned: `220 / 220`
- ID hints: `15` split pictures, `0` merged pictures
- Render failures: `0`

Slowest vector pages: 19 (`0.344 s`), 38 (`0.265 s`), 70 (`0.254 s`),
145 (`0.254 s`), and 39 (`0.242 s`).

Reference PNGs were visually checked. Page 85's two figures are separate and
their captions are outside the crops.
