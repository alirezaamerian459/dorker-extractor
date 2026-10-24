from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ScanConfig:
    target_url: str
    keywords: tuple[str, ...]
    templates: tuple[str, ...]
    extensions: tuple[str, ...]
    max_pages: int = 50
    concurrency: int = 3
    request_delay: float = 1.0
    use_playwright: bool = False
    user_agent: str = "DorkLinkExtractor/1.0 (+authorized public-site audit)"


@dataclass
class FileLink:
    url: str
    source_page: str
    anchor_text: str = ""
    extension: str = ""
    http_status: int | None = None
    content_type: str = ""
    content_length: int | None = None
    reachable: bool = False
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "source_page": self.source_page,
            "anchor_text": self.anchor_text,
            "extension": self.extension,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "reachable": self.reachable,
            "checked_at": self.checked_at,
            "error": self.error,
        }


@dataclass
class ScanSummary:
    scan_id: int
    pages_visited: int
    files_found: int
    files_reachable: int
    errors: int
    started_at: str
    finished_at: str
