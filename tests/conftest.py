"""Point tests at the language files downloaded next to the repository."""

from __future__ import annotations

import os
from pathlib import Path

_tessdata = Path(__file__).resolve().parents[1] / "tessdata"
if not os.environ.get("TESSDATA_PREFIX") and (_tessdata / "eng.traineddata").is_file():
    os.environ["TESSDATA_PREFIX"] = str(_tessdata)
