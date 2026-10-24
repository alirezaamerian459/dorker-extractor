from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from .scope import host_in_scope

log = logging.getLogger(__name__)


class GoogleSearchProvider:
    """Best-effort parser for public Google HTML results; Google may rate-limit automation."""

    endpoint = "https://www.google.com/search"

    def __init__(self, client: httpx.AsyncClient, target_host: str, delay: float = 1.0, warning=None):
        self.client = client
        self.target_host = target_host
        self.delay = max(delay, 0.5)
        self.warning = warning
        self._last_query = 0.0

    async def search(self, query: str, limit: int = 10) -> list[str]:
        now = asyncio.get_running_loop().time()
        wait = self.delay - (now - self._last_query)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_query = asyncio.get_running_loop().time()
        try:
            response = await self.client.get(self.endpoint, params={"q": query, "num": min(limit, 10)})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Search request failed for %r: %s", query, exc)
            if self.warning:
                self.warning(f"Google search failed: {exc}")
            return []
        soup = BeautifulSoup(response.text, "lxml")
        found: list[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            parsed_href = urlparse(href)
            if href.startswith("/url?") or (
                (parsed_href.hostname or "").lower() in {"google.com", "www.google.com"}
                and parsed_href.path == "/url"
            ):
                params = parse_qs(parsed_href.query)
                href = params.get("q", params.get("url", params.get("u", [""])))[0]
            elif href.startswith("/url/"):
                continue
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not host_in_scope(href, self.target_host):
                continue
            cleaned = parsed._replace(fragment="").geturl()
            if cleaned not in found:
                found.append(cleaned)
                if len(found) >= limit:
                    break
        if not found and any(marker in response.text.casefold() for marker in ("unusual traffic", "recaptcha", "captcha")):
            if self.warning:
                self.warning("Google returned a CAPTCHA or automated-traffic page; search results may be incomplete.")
        return found


class DuckDuckGoSearchProvider:
    """Secondary public HTML search provider for broader result coverage."""

    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(self, client: httpx.AsyncClient, target_host: str, delay: float = 1.0, warning=None):
        self.client = client
        self.target_host = target_host
        self.delay = max(delay, 0.5)
        self.warning = warning
        self._last_query = 0.0

    async def search(self, query: str, limit: int = 10) -> list[str]:
        now = asyncio.get_running_loop().time()
        wait = self.delay - (now - self._last_query)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_query = asyncio.get_running_loop().time()
        try:
            response = await self.client.get(self.endpoint, params={"q": query})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("DuckDuckGo search request failed for %r: %s", query, exc)
            if self.warning:
                self.warning(f"DuckDuckGo search failed: {exc}")
            return []
        soup = BeautifulSoup(response.text, "lxml")
        found: list[str] = []
        for anchor in soup.select("a.result__a[href], a[href]"):
            href = anchor.get("href", "")
            parsed = urlparse(href)
            if href.startswith("/l/?"):
                href = parse_qs(parsed.query).get("uddg", [""])[0]
            elif "duckduckgo.com" in (parsed.hostname or "").lower():
                href = parse_qs(parsed.query).get("uddg", [""])[0]
            elif href.startswith("//"):
                href = "https:" + href
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not host_in_scope(href, self.target_host):
                continue
            cleaned = parsed._replace(fragment="").geturl()
            if cleaned not in found:
                found.append(cleaned)
                if len(found) >= limit:
                    break
        if not found and any(marker in response.text.casefold() for marker in ("anomaly-modal", "bots use", "challenge-form")):
            if self.warning:
                self.warning("DuckDuckGo returned an automated-traffic challenge; search results may be incomplete.")
        return found


def build_queries(keywords: tuple[str, ...], templates: tuple[str, ...], target_host: str, extension: str | tuple[str, ...] = "pdf") -> list[str]:
    effective_templates = templates or ("site:{site} {keyword}",)
    extensions = (extension,) if isinstance(extension, str) else (extension or ("pdf",))
    queries: list[str] = []
    for keyword in keywords:
        for template in effective_templates:
            selected_extensions = extensions if "{ext}" in template else (extensions[0],)
            for ext in selected_extensions:
                query = (template.replace("{keyword}", keyword)
                         .replace("{site}", target_host)
                         .replace("{target}", target_host)
                         .replace("{ext}", ext.lstrip(".")).strip())
                if "{site}" not in template and "{target}" not in template:
                    query = f"site:{target_host} {query}"
                if query and query not in queries:
                    queries.append(query)
    return queries
