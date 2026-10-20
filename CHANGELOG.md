# Changelog

## 0.5.0

- Return vector drawings as downloadable PNG pictures with
  `kind=vector_drawing`; embedded images use `kind=embedded_raster`.
- Add separate raster and vector count and time limits. The default maximum is
  80 rasters plus 120 vectors. Setting `MAX_PICTURES` keeps the previous
  operator-controlled total cap across both kinds.
- Report counts by kind, vector detector, ID-based split/merge hints, scanned
  vector pages, and the five slowest vector pages.
- Resolve indirect PDF image stream lengths instead of dropping those rasters.
- Keep picture-text repair opt-in with `repair_picture_text=true`.
