# CAT manual acceptance — 2026-10-20

Local HTTP run of the 530-page `cat-operator-manual-320-323.pdf`.

- PDF SHA-256: `5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab`
- Text request: `33.06 s`
- Full request: `40.47 s` (`1.22x` text-only)
- Picture downloads: `0.05 s`
- Peak RSS: text `776.3 MiB`; full `1070.1 MiB`
- Non-empty text pages: `529`
- GET results: `84` succeeded, `0` failed

## Picture report

- Embedded rasters: `80` listed, `788` skipped by the existing count limit
- Vector drawings: `4` listed, `0` skipped
- Vector pages scanned: `530 / 530`
- ID hints: `0` split pictures, `0` merged pictures
- Render, decode, storage, and unreadable failures: `0`

Slowest vector pages: 74 (`0.269 s`), 95 (`0.260 s`), 85 (`0.228 s`),
98 (`0.225 s`), and 93 (`0.222 s`).

Raster byte comparison passed on focus pages 24 and 26, including four
same-sized images on page 24 whose API order differs from PDF xref order.
Pages 27, 419, and 420 matched the expected post-limit behavior.
