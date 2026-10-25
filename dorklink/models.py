from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "dclid", "fbclid", "gbraid", "gclid", "igshid", "mc_cid", "mc_eid",
    "msclkid", "wbraid", "yclid", "_ga", "_gl",
}


def file_link_dedupe_key(url: str) -> str:
    """Normalize a file URL for duplicate detection without dropping download parameters."""
    original = str(url or "").strip()
    try:
        parsed = urlsplit(original)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, "")) or original

        port = parsed.port
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc_host = f"[{host}]" if ":" in host else host
        netloc = netloc_host if not port or default_port else f"{netloc_host}:{port}"

        # Fragments and common analytics parameters do not identify a different file.
        # Keep signed, session, and other functional query parameters untouched.
        query_parts = []
        for part in parsed.query.split("&") if parsed.query else ():
            raw_key = part.split("=", 1)[0]
            key = unquote_plus(raw_key).casefold()
            if key.startswith("utm_") or key in _TRACKING_QUERY_KEYS:
                continue
            query_parts.append(part)
        return urlunsplit((scheme, netloc, parsed.path or "/", "&".join(query_parts), ""))
    except ValueError:
        return original


def merge_unique_text(current: str, incoming: str, *, case_sensitive: bool = False) -> str:
    values = [value.strip() for value in str(current or "").splitlines() if value.strip()]
    identity = (lambda value: value) if case_sensitive else str.casefold
    known = {identity(value) for value in values}
    for value in str(incoming or "").splitlines():
        value = value.strip()
        key = identity(value)
        if value and key not in known:
            values.append(value)
            known.add(key)
    return "\n".join(values)


def merge_link_evidence(item: "FileLink", source_page: str, anchor_text: str) -> bool:
    """Retain distinct source pages and link labels when duplicate URLs collapse."""
    source_pages = merge_unique_text(item.source_page, source_page, case_sensitive=True)
    anchor_texts = merge_unique_text(item.anchor_text, anchor_text)
    changed = source_pages != item.source_page or anchor_texts != item.anchor_text
    item.source_page = source_pages
    item.anchor_text = anchor_texts
    return changed


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
