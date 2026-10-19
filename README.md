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

The picture list is embedded raster images only. It does not include vector drawings, diagrams inferred from page layout, or a rendered scan page. Rasters under 100 pixels on a side are ignored and are not counted as omissions. A raster is skipped before decode when its compressed stream is over 8 MiB, that length cannot be measured, or its pixel size would exceed the 64 MiB decode budget. At most 80 pictures are listed. The in-memory store for all PDFs together stays within 64 MiB (`MAX_PICTURE_STORE_BYTES`). A picture that does not fit is omitted with reason `storage`. `PICTURE_EXTRACT_TIMEOUT_SECONDS` (default 20) stops the cut between pictures. It does not interrupt one picture that has already started decoding. `picture_report.reasons` counts omissions as `time`, `count`, `encoded_size`, `decoded_size`, `unreadable`, or `storage`. `truncated` is true when any eligible embedded raster was omitted. `GET /health` includes `picture_store_bytes` and `picture_store_ids`.

```json
{
  "page_count": 2,
  "pages": [
    {"page": 1, "markdown": "..."}
  ],
  "pictures": [
    {"id": "picture-id", "page": 1, "index_on_page": 0, "width": 800, "height": 600}
  ],
  "picture_report": {
    "scope": "embedded_raster",
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
      "storage": 0
    }
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
