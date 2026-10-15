#!/usr/bin/env python3
"""Download the full Tesseract fast language set.

PyMuPDF already contains the Tesseract engine. These files are the
dictionaries it reads. The destination is ``TESSDATA_PREFIX``, or
``tessdata/`` in this repository when that variable is unset.
"""

from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_URL = (
    "https://github.com/tesseract-ocr/tessdata_fast/archive/"
    f"{TESSDATA_COMMIT}.tar.gz"
)
# eng, rus, and a language code with an underscore must all be present.
_REQUIRED = ("eng.traineddata", "rus.traineddata", "chi_sim.traineddata")


def destination() -> Path:
    configured = os.environ.get("TESSDATA_PREFIX")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "tessdata"


def _already_complete(dest: Path) -> bool:
    if not all((dest / name).is_file() for name in _REQUIRED):
        return False
    return sum(1 for _ in dest.rglob("*.traineddata")) >= 100


def fetch(dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        TESSDATA_URL,
        headers={"User-Agent": "pymupdf4llm-service-tessdata-fetch"},
    )
    written = 0
    with tempfile.TemporaryFile() as downloaded:
        with urllib.request.urlopen(request, timeout=120) as response:
            shutil.copyfileobj(response, downloaded)
        downloaded.seek(0)
        with tarfile.open(fileobj=downloaded, mode="r:gz") as archive:
            for member in archive:
                relative = _traineddata_path(member.name)
                if relative is None:
                    continue
                payload = _regular_member(archive, member)
                if payload is None:
                    continue
                target = dest.joinpath(relative)
                if not target.resolve().is_relative_to(dest.resolve()):
                    continue
                source = archive.extractfile(payload)
                if source is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                written += 1
                if written % 20 == 0:
                    print(f"downloaded {written} language files", flush=True)
    return written


def _regular_member(archive: tarfile.TarFile, member: tarfile.TarInfo, depth: int = 0):
    """Follow a dictionary that the archive stores as a symlink."""
    if member.isfile():
        return member
    if depth > 5 or not (member.issym() or member.islnk()):
        return None
    target_name = os.path.normpath(str(Path(member.name).parent / member.linkname))
    try:
        target = archive.getmember(target_name)
    except KeyError:
        return None
    return _regular_member(archive, target, depth + 1)


def _traineddata_path(name: str) -> Path | None:
    parts = Path(name).parts
    if len(parts) < 2 or ".." in parts:
        return None
    relative = Path(*parts[1:])
    if relative.suffix != ".traineddata":
        return None
    return relative


def main() -> int:
    dest = destination()
    if _already_complete(dest):
        count = sum(1 for _ in dest.rglob("*.traineddata"))
        print(f"{count} language files already in {dest}")
        return 0
    written = fetch(dest)
    if not _already_complete(dest):
        print(f"language data in {dest} is incomplete ({written} files)", file=sys.stderr)
        return 1
    print(f"downloaded {written} language files into {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
