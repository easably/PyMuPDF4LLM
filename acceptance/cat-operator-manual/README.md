# CAT manual acceptance

Manual check of one real manual, `cat-operator-manual-320-323.pdf`. Keep it on disk (this repo uses `tessdata/pdfs/`). It is not in git, and pytest does not read it.

Expected file:

- SHA-256 `5c0fdb73e305224777d690543f3394246a8da252577e7b97ac1d3f8724d017ab`
- 530 pages

The script checks both before any HTTP call. If the path is missing it tells you to pass `--pdf` or `CAT_MANUAL_PDF`.

Recorded runs live in `acceptance/cat-operator-manual/runs/`, one file per date and host. The 16 October 2026 run on server1 is `runs/2026-10-16-server1.md`. The size-eligible image count in a run is an inventory of the PDF. It is not the number of files the service is expected to return. Raster and vector caps are separate by default; setting `MAX_PICTURES` applies a total cap. Raster extraction and vector detection have separate time limits and share the decoded-byte and in-memory store budgets.

Quality investigations live in `analyses/`. The comparison of the current OCR
policy, no OCR, and page text thresholds is
`analyses/2026-10-16-ocr-policy.md`.

## Vector holdout

`holdout-labels.json` is a second-manual check against overfitting on the
compact track loader. Run it with the same metric script:

```bash
.venv/bin/python scripts/accept_compact_track_loader.py \
  --pdf tessdata/pdfs/cat-operator-manual-320-323.pdf \
  --labels acceptance/cat-operator-manual/holdout-labels.json \
  --candidate-source pymupdf4llm_service.vector_drawings:vector_candidates \
  --mixed-candidate-source pymupdf4llm_service.vector_drawings:all_picture_candidates \
  --report acceptance/cat-operator-manual/runs/2026-10-20-holdout.md
```

The 2026-10-20 holdout found mixed-page raster recall 100% and precision 90%,
with three logo/header false vectors and one table false vector. Path-heavy
table pages stayed empty. This manual has almost no vector-only drawings, so
it is a false-positive / raster check, not a second vector recall corpus.

## Local

Start the service yourself, then point the script at it.

```bash
PYTHONPATH=src .venv/bin/uvicorn pymupdf4llm_service.app:app --port 8080
```

```bash
.venv/bin/python scripts/accept_cat_manual.py \
  --mode local \
  --base-url http://127.0.0.1:8080 \
  --pdf tessdata/pdfs/cat-operator-manual-320-323.pdf
```

Inventory only, no HTTP:

```bash
.venv/bin/python scripts/accept_cat_manual.py \
  --inventory-only \
  --pdf tessdata/pdfs/cat-operator-manual-320-323.pdf
```

## After deploy

Run this on the deploy host, or through an SSH tunnel to a port bound on that host's loopback. Do not publish the port on the internet.

Use a second container started from the same image as production. Do not POST this PDF to `http://pymupdf4llm:8080`. That name is the live Doc2Graph target. The service converts one PDF at a time, so this file would block ordinary requests. The script refuses that host unless `--allow-shared-service` is set.

```bash
docker network create pymupdf4llm-accept
docker run -d --name pymupdf4llm-accept \
  --network pymupdf4llm-accept \
  --memory 4g --cpus 8 \
  ghcr.io/easably/pymupdf4llm:4803114
```

Match `--memory` and `--cpus` to the live container when you re-run this later. Poll `docker stats` into a file while the script runs, and pass that file as `--memory-log`. One sample per second is enough. The script reads the peak during each phase.

Run the script inside another container on that network so the host does not need PyMuPDF installed. Mount the PDF read-only. `--user root` is only so the mounted script can be read when the image's default user cannot:

```bash
docker run --rm --network pymupdf4llm-accept --user root \
  -v "$PWD:/src:ro" \
  -v /tmp/accept-mem.log:/tmp/accept-mem.log \
  -v /tmp/accept-out:/out \
  ghcr.io/easably/pymupdf4llm:4803114 \
  python /src/scripts/accept_cat_manual.py \
    --mode deploy \
    --base-url http://pymupdf4llm-accept:8080 \
    --pdf /src/tessdata/pdfs/cat-operator-manual-320-323.pdf \
    --memory-log /tmp/accept-mem.log \
    --report /out/2026-10-16-server1.md \
    --meta-host "server1" \
    --meta-image "ghcr.io/easably/pymupdf4llm:4803114" \
    --meta-commit "480311497b1e51b64136f1e4be2319af835527a9"
```

From a laptop, tunnel only the acceptance container's loopback port if you published `127.0.0.1:18080:8080` on the server. Then `--mode deploy --base-url http://127.0.0.1:18080`. Remove the acceptance container and network when the run is finished.

The script prints timings and checks the responses. It does not fail the run for being slow. There is no time threshold until several runs agree.

What it prints:

- local inventory time
- text-only POST time (`include_pictures=false`)
- full POST time (`include_pictures=true`, text plus pictures)
- time to GET every returned id
- response size, non-empty text pages, picture count, GET success and failure
- peak container memory, when `--memory-log` is a `docker stats` log

This image does not split text time and picture time inside one response. The text-only POST is the text measurement. The full POST measures the call Doc2Graph makes when it wants pictures. Subtracting the two is not a timer from inside the process.

## Consumer contract

The consumer is Doc2Graph doc-ingest. It already speaks this contract. This acceptance run does not change the service.

| Question | What the consumer does |
| --- | --- |
| Incomplete picture list | It keeps the text. `picture_report.truncated`, `by_kind`, and `reasons` (`time`, `count`, `encoded_size`, `decoded_size`, `unreadable`, `storage`, `render_failure`, `too_small_displayed`) are stored on the ingest attempt. A failed GET is an extra omission. The PDF is not sent again to fill the gap. |
| Download time | One client timeout covers the POST and every GET. The setting is `pymupdf4llm_timeout_seconds`, default 600 seconds. Picture ids are fetched one after another inside that same timeout. |
| One-time id | The first successful GET deletes that id. A repeat GET is 404. The consumer fetches each id once. |
| Restart | Ids live only in the process, for ten minutes if nobody fetches them. A restart or a second instance drops them. The consumer must fetch from the same instance before that. |
| Pictures on every text request | No. `include_pictures=true` is sent only when figure indexing is on. A text-only ingest leaves the flag false, and the service does not cut pictures. |

Masks and rasters under 100 pixels on a side are left out of both `listed` and `skipped`. Rasters shown smaller than 40 PDF points are omitted with `too_small_displayed`. The consumer treats `truncated: false` as "every eligible raster or detected vector drawing was listed", which matches that rule.

Scan pages rendered as a whole page, and small decorative marks, are not in this list. Vector drawings are returned as `kind=vector_drawing`; embedded images use `kind=embedded_raster`.

No extra truncation field is required for the consumer to see a short list. `truncated` and `reasons` are already on the response. The 16 October 2026 run on server1 confirmed that: the manual came back `truncated: true` with `reasons.count` 788, and Doc2Graph already records those fields. See `runs/2026-10-16-server1.md`.

Copy the `--report` file into `acceptance/cat-operator-manual/runs/` and name it by date and host (`2026-10-16-server1.md`). Do not put that file in `plans/`.
