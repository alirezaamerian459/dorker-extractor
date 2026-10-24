from __future__ import annotations

import csv
from pathlib import Path

from .models import FileLink

CSV_FIELDS = ["url", "source_page", "anchor_text", "extension", "http_status", "content_type", "content_length", "reachable", "checked_at", "error"]


def export_csv(path: str | Path, links: list[FileLink]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for item in links:
            writer.writerow(item.to_dict())
