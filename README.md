# PyMuPDF4LLM text service

HTTP service that turns a PDF into markdown for a language model. Text pages are copied from the text layer. A page with no selectable text is recognized by the Tesseract engine built into PyMuPDF, using the language files shipped in this image. Callers do not import the library.

License: [AGPL-3.0](LICENSE). Source for this service: https://github.com/easably/PyMuPDF4LLM

This program is free software under the GNU Affero General Public License version 3. Running it as a network service requires offering this source to users who interact with it.

## Call

`GET /health`

`POST /v1/convert` with multipart field `file` (a PDF). Optional form field `ocr_language` (default `eng`). Any language in the image is accepted, including codes such as `rus`, `deu`, `chi_sim`, or a combination such as `eng+rus`. Optional form field `include_pictures` (default `false`). Text-only clients leave it false. Picture bytes are cut only when it is true, in the same PDF open as the text.

Optional form field `repair_picture_text` is also `false` by default. When
enabled, it restores text that page layout incorrectly put inside
`picture text` blocks. Raster-picture text is left unchanged. This is an
opt-in feature: in multi-column procedures, restored side-column answers may
follow the main column instead of appearing beside their related question.

`page` is 1-based. Empty pages are omitted. `force_ocr=true` is rejected. Picture bytes are not in the JSON. Fetch each listed id with `GET /v1/pictures/{id}` (`image/png`). Each occurrence has its own id, including a logo repeated on another page. Ids are transient and exist only in this process. Run one instance unless a shared store is added on purpose. A fetched id is removed. Unfetched ids expire after ten minutes.

The picture list contains embedded rasters and vector drawings, identified by
`kind`. It does not contain a rendered whole page. Rasters under 100 pixels on
a side are ignored and are not counted as omissions. A raster shown smaller
than `MIN_PICTURE_DISPLAY_SIDE_PT` (default 40 PDF points) on either side is
omitted with reason `too_small_displayed`, so a high-resolution icon drawn as
a small CAUTION mark does not fill the raster cap. Raster and vector count
limits are separate (`MAX_RASTER_PICTURES`, default 80, and
`MAX_VECTOR_PICTURES`, default 120), so the default maximum is 200. If
`MAX_PICTURES` is set, it remains a total cap across both kinds. Decoded PNG
bytes and the 64 MiB in-memory store are shared.
`PICTURE_EXTRACT_TIMEOUT_SECONDS` (default 20) limits raster extraction;
`VECTOR_DETECTION_TIMEOUT_SECONDS` (default 60) separately limits the vector
page scan. Both are checked between units of work. `picture_report.reasons` counts
known omissions as `time`, `count`, `encoded_size`, `decoded_size`,
`unreadable`, `storage`, `render_failure`, or `too_small_displayed`.
`raster_cap_reached_at_page` is the 1-based page where the raster count cap
first stopped listing. `by_kind` separates raster and vector totals, and
`vector_detectors` reports listed layout and fallback detections.
`vector_pages_scanned` and `vector_pages_total` make an incomplete
long-document scan explicit; `vector_slowest_pages` contains its five slowest
pages. `truncated` is true when an eligible picture was omitted or the vector
scan did not reach the last page.
Run the container with 4 GiB of memory (`PYMUPDF4LLM_MEM_LIMIT`, default
`4g` in the Doc2Graph overlay). A 530-page convert with pictures peaked at
about 1.1 GiB, so the default byte caps do not need lowering.
`GET /health` includes `picture_store_bytes` and `picture_store_ids`.

Vector detection thresholds can be overridden with these environment
variables:

- `VECTOR_MINIMUM_SIDE`, `VECTOR_MINIMUM_PATHS`,
  `VECTOR_MAXIMUM_PAGE_AREA_SHARE`,
  `VECTOR_FALLBACK_MAXIMUM_PAGE_AREA_SHARE`
- `VECTOR_TABLE_OVERLAP_SHARE`, `VECTOR_COVERED_PATH_SHARE`,
  `VECTOR_DUPLICATE_IOU`, `VECTOR_MARGIN`
- `VECTOR_CLUSTER_X_TOLERANCE`, `VECTOR_CLUSTER_Y_TOLERANCE`,
  `VECTOR_PREFILTER_MIN_PATHS`
- `VECTOR_FALLBACK_TABLE_MIN_WIDTH_SHARE`,
  `VECTOR_FALLBACK_TABLE_MIN_AREA_SHARE`,
  `VECTOR_FALLBACK_TABLE_WITHOUT_ID_OVERLAP_SHARE`,
  `VECTOR_FALLBACK_FIGURE_ID_MARGIN`
- `VECTOR_MERGE_FIGURE_ID_MARGIN`, `VECTOR_MERGE_HORIZONTAL_GAP`,
  `VECTOR_MERGE_VERTICAL_GAP`, `VECTOR_MERGE_OVERLAP_SHARE`
- `VECTOR_SPLIT_VERTICAL_END_OFFSET`, `VECTOR_SPLIT_VERTICAL_START_OFFSET`,
  `VECTOR_SPLIT_HORIZONTAL_END_OFFSET`, `VECTOR_SPLIT_HORIZONTAL_START_OFFSET`,
  `VECTOR_SPLIT_MARGIN`, `VECTOR_SPLIT_PATH_MIN_EXTENT`

`VECTOR_FIGURE_ID_PATTERNS` is an optional `;;`-separated list of regular
expressions. Its default recognizes `TX…` and `TS…` IDs from the reference
manual. IDs are only hints for splitting and merging; when none match, the
detector falls back to layout and geometry.

```json
{
  "page_count": 2,
  "pages": [
    {"page": 1, "markdown": "..."}
  ],
  "pictures": [
    {"id": "picture-id", "page": 1, "index_on_page": 0, "width": 800, "height": 600, "kind": "vector_drawing"}
  ],
  "picture_report": {
    "scope": "embedded_raster_and_vector_drawing",
    "requested": true,
    "truncated": false,
    "listed": 1,
    "skipped": 0,
    "reasons": {
      "time": 0,
      "count": 0,
      "encoded_size": 0,
      "decoded_size": 0,
      "unreadable": 0,
      "storage": 0,
      "render_failure": 0,
      "too_small_displayed": 0
    },
    "raster_cap_reached_at_page": null,
    "by_kind": {
      "embedded_raster": {"listed": 0, "skipped": 0},
      "vector_drawing": {"listed": 1, "skipped": 0}
    },
    "vector_detectors": {"layout": 1, "fallback": 0},
    "vector_id_actions": {"split": 0, "merged": 0},
    "vector_pages_scanned": 2,
    "vector_pages_total": 2,
    "vector_slowest_pages": [{"page": 1, "seconds": 0.012345}]
  }
}
```

The image contains the full Tesseract fast language set. `TESSDATA_PREFIX` inside the container is `/usr/share/tessdata`. `MAX_CONCURRENT_CONVERTS` (default `1`) limits how many PDFs are converted at once. A slot stays taken until that conversion thread finishes, even if the caller disconnects. That disconnected conversion keeps its queue place until the thread finishes. `MAX_QUEUED_CONVERTS` (default `1`) limits how many may wait. A further upload gets `429`. `GET /health` keeps answering during a conversion.

On a Docker network the address is `http://pymupdf4llm:8080`. The port does not need to be published to the internet.

## Image

GitHub Actions builds the image and, on a push to `main`, pushes it to GitHub Container Registry:

`ghcr.io/easably/pymupdf4llm:latest`

While this GitHub repository is private, that container package stays private. A pull needs a GitHub token with `read:packages`.

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u USERNAME --password-stdin
docker pull ghcr.io/easably/pymupdf4llm:latest
docker run --rm -p 8080:8080 ghcr.io/easably/pymupdf4llm:latest
```

Do not change the package visibility to public unless you mean to publish the image.

## Local

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
python3 scripts/fetch_tessdata.py
.venv/bin/pytest
PYTHONPATH=src .venv/bin/uvicorn pymupdf4llm_service.app:app --port 8080
```

`fetch_tessdata.py` writes the language files to `tessdata/` (or to `TESSDATA_PREFIX`, when that variable is set). That directory is not committed.

## Acceptance run

`scripts/accept_cat_manual.py` is a manual check for one long local PDF. pytest and CI do not run it and do not need the file. How to run it, and the recorded runs, are in `acceptance/cat-operator-manual/`.
