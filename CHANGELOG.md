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
- Skip rasters whose shown box is under `MIN_PICTURE_DISPLAY_SIDE_PT`
  (default 40 pt) so small CAUTION icons do not fill the raster cap.
  `raster_cap_reached_at_page` records where the cap stopped listing.
- Reject vector candidates in layout `page-header` / `page-footer` or in the
  top/bottom page edge band (default 8% of page height) unless a figure id or
  caption is nearby; drop small-area logo marks without evidence; do not
  resurrect layout regions rejected as `text_only`.
- Keep picture-text repair opt-in with `repair_picture_text=true`.
