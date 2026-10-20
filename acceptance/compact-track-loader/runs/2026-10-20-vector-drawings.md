# Vector drawing acceptance — 2026-10-20 (review 2)

- PDF SHA-256: `ef29c72b6659a61f2d7905ffcbe7ec03375e45ae37c84a801a44f3940edcdf14`
- IoU threshold: `0.5`
- Layout time: `20.32 s`
- Peak process RSS during the labeled-page run: `561.0 MiB`

## Detection

| Group | Drawings | Candidates | Matches | False | Recall | Precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| vector-only | 41 | 41 | 41 | 0 | 100.0% | 100.0% |
| mixed (raster + vector) | 15 | 15 | 15 | 0 | 100.0% | 100.0% |
| negatives | 0 | 0 | 0 | 0 | n/a | n/a |

False pictures: none. Pages 99, 100, and 178 leftover fragments are absorbed
into the neighboring drawing. Page 184's dial label was tightened to the
graphics. CAUTION icons are no longer mixed-page candidates.

## Full conversion

- Text only: `23.23 s`
- Text plus pictures: `30.46 s`
- Ratio: `1.31x`
- Peak process RSS: text `597.4 MiB`; full `750.7 MiB`
- Embedded rasters: `80` listed, `43` skipped as `too_small_displayed`
  (CAUTION triangle, xref 17), `169` skipped by the raster count cap
- `raster_cap_reached_at_page`: `58` (last listed raster is on page 57)
- Vector PNGs: `67` listed (`53` layout, `14` fallback), `0` skipped
- Vector pages scanned: `220 / 220`
- ID hints: `15` split pictures, `0` merged pictures
- Render failures: `0`

## Raster cap decision

Keep `MAX_RASTER_PICTURES=80`. After dropping the CAUTION icons this file
still has 249 displayed-large rasters. Raising the default would send more
images to doc2graph captions. Operators can raise the env var per deploy.

## Prefilter

`VECTOR_PREFILTER_MIN_PATHS` 1, 5, and 10 all keep 100% recall on the
labeled vector-only pages (those pages have layout `picture` boxes). Default
stays 1.
