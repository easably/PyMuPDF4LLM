# PyMuPDF4LLM text service

HTTP service that turns a PDF into markdown for a language model. Text pages are copied from the text layer. A page with no selectable text is recognized by the Tesseract engine built into PyMuPDF, using the language files shipped in this image. Callers do not import the library.

License: [AGPL-3.0](LICENSE). Source for this service: https://github.com/easably/PyMuPDF4LLM

This program is free software under the GNU Affero General Public License version 3. Running it as a network service requires offering this source to users who interact with it.

## Call

`GET /health`

`POST /v1/convert` with multipart field `file` (a PDF). Optional form field `ocr_language` (default `eng`). Any language in the image is accepted, including codes such as `rus`, `deu`, `chi_sim`, or a combination such as `eng+rus`.

```json
{
  "page_count": 2,
  "pages": [
    {"page": 1, "markdown": "..."}
  ]
}
```

`page` is 1-based. Empty pages are omitted. `force_ocr=true` is rejected.

The image contains the full Tesseract fast language set. `TESSDATA_PREFIX` inside the container is `/usr/share/tessdata`. `MAX_CONCURRENT_CONVERTS` (default `1`) limits how many PDFs are converted at once. `MAX_QUEUED_CONVERTS` (default `1`) limits how many may wait. A further upload gets `429`. `GET /health` keeps answering during a conversion.

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
