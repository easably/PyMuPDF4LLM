# CAT manual acceptance — 2026-10-20 (review 2)

Local HTTP run of the 530-page `cat-operator-manual-320-323.pdf`.

- PDF SHA-256: `5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab`
- Text request: `34.39 s`
- Full request: `41.18 s` (`1.20x` text-only)
- Picture downloads: `0.05 s`
- Non-empty text pages: `529`
- GET results: `84` succeeded, `0` failed

## Picture report

- Embedded rasters: `80` listed, `788` skipped by the count limit
- Shown smaller than 40 pt: `0`
- `raster_cap_reached_at_page`: `111`
- Vector drawings: `4` listed, `0` skipped
- Vector pages scanned: `530 / 530`
- ID hints: `0` split pictures, `0` merged pictures
- Render, decode, storage, and unreadable failures: `0`

This manual has no CAUTION-style icons under 40 pt, so the new displayed-size
filter does not change its raster list. Peak RSS on the earlier local full
run was `1070 MiB`, well under the 4 GiB deploy `mem_limit`. Do not lower
the default PNG or store caps.

## Raster cap decision

Keep `MAX_RASTER_PICTURES=80`. 868 size-eligible rasters remain after static
filters; the cap still stops listing at page 111. Raising it is a consumer
caption-cost choice.

Raster byte comparison passed on focus pages 24 and 26. Pages 27, 419, and
420 matched the expected post-limit behavior.
