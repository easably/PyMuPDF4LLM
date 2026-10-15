#!/usr/bin/env python3
"""Call a running service over HTTP and check a text page and a scan page."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.text_layer_pdf import mixed_pdf, text_layer_pdf


def wait_until_healthy(client: httpx.Client) -> None:
    last_error = "no response"
    for _ in range(60):
        try:
            response = client.get("/health")
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last_error = response.text
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SystemExit(f"health did not answer: {last_error}")


def convert(client: httpx.Client, pdf: bytes, language: str) -> str:
    response = client.post(
        "/v1/convert",
        files={"file": ("manual.pdf", pdf, "application/pdf")},
        data={"ocr_language": language},
        timeout=120,
    )
    if response.status_code != 200:
        raise SystemExit(f"{language} -> {response.status_code} {response.text}")
    return "\n".join(page["markdown"] for page in response.json()["pages"])


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    with httpx.Client(base_url=base_url, timeout=30) as client:
        wait_until_healthy(client)
        english = convert(
            client,
            mixed_pdf("Maintenance interval 10 hours", "Nameplate serial 4821"),
            "eng",
        )
        if "10 hours" not in english or "Nameplate serial 4821" not in english:
            raise SystemExit(f"english pages missing text: {english!r}")
        russian = convert(
            client,
            mixed_pdf("Maintenance interval 10 hours", "Насос масла", cyrillic_scan=True),
            "rus",
        )
        if "Насос масла" not in russian:
            raise SystemExit(f"russian scan missing text: {russian!r}")
        german = convert(client, text_layer_pdf("Maintenance interval 10 hours"), "deu")
        if "10 hours" not in german:
            raise SystemExit(f"german code was not accepted: {german!r}")
        chinese = convert(client, text_layer_pdf("Maintenance interval 10 hours"), "chi_sim")
        if "10 hours" not in chinese:
            raise SystemExit(f"chinese code was not accepted: {chinese!r}")
    print("http smoke ok")


if __name__ == "__main__":
    main()
