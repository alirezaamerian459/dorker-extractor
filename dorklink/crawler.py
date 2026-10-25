from __future__ import annotations

import asyncio
import ipaddress
import logging
import mimetypes
import re
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urldefrag, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .models import FileLink, ScanConfig, file_link_dedupe_key, merge_link_evidence, merge_unique_text
from .scope import host_in_scope, normalize_target, safe_http_url
from .search import DuckDuckGoSearchProvider, GoogleSearchProvider, build_queries
from .storage import ScanStore

log = logging.getLogger(__name__)
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
NON_PAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2", "ttf", "xml", "json", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt", "zip", "rar", "7z", "exe", "msi", "dmg", "iso"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_extension(url: str) -> str:
    parsed = urlparse(url)
    path_suffix = PurePosixPath(unquote(parsed.path).replace("\\", "/")).suffix.lower().lstrip(".")
    if path_suffix and path_suffix not in {"php", "asp", "aspx", "jsp", "cgi", "do", "ashx"}:
        return path_suffix

    # Download handlers often keep the real filename in a query parameter.
    # This also lets search-result URLs such as ?file=report.pdf be recognized.
    query = parse_qs(parsed.query)
    filename_keys = {
        "filename", "filename*", "file", "file_name", "name", "attachment",
        "attachment_filename", "document", "download", "download_url",
    }
    for key, values in query.items():
        if key.lower() not in filename_keys:
            continue
        for value in values:
            value = unquote(value)
            disposition_filename = re.search(
                r"filename\*?=(?:UTF-8''|\")?([^;\"]+)", value, flags=re.IGNORECASE
            )
            candidate = disposition_filename.group(1).strip().strip('"') if disposition_filename else value
            candidate_path = urlparse(candidate).path if "://" in candidate else candidate
            suffix = PurePosixPath(candidate_path.replace("\\", "/")).suffix.lower().lstrip(".")
            if suffix:
                return suffix
    return "" if path_suffix in {"php", "asp", "aspx", "jsp", "cgi", "do", "ashx"} else path_suffix


DOWNLOAD_HINTS = (
    "download", "attachment", "export", "get file", "دانلود", "دریافت",
    "بارگیری", "تحميل",
)


def _has_download_hint(value: str) -> bool:
    normalized = _clean_text(value).casefold()
    return any(hint in normalized for hint in DOWNLOAD_HINTS)


def _looks_like_download_endpoint(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return bool(re.search(r"(?:^|[/_-])(?:download|downloads|attachment|files?|dl|export)(?:[/_.-]|$)", path))


def _is_external_http_url(url: str, target_host: str) -> bool:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if host_in_scope(url, target_host) or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def _clean_text(value: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


@asynccontextmanager
async def _response_context(response: httpx.Response):
    try:
        yield response
    finally:
        await response.aclose()


class _RequestGate:
    def __init__(self, delay: float, cancelled):
        self.delay = max(0.0, delay)
        self.cancelled = cancelled
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            pause = self.delay - (now - self._last)
            while pause > 0 and not self.cancelled():
                await asyncio.sleep(min(pause, 0.25))
                now = asyncio.get_running_loop().time()
                pause = self.delay - (now - self._last)
            self._last = asyncio.get_running_loop().time()


class _Robots:
    def __init__(self, parser: RobotFileParser, user_agent: str):
        self.parser = parser
        self.user_agent = user_agent

    def can_fetch(self, url: str) -> bool:
        try:
            return self.parser.can_fetch(self.user_agent, url)
        except Exception:
            return True


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        if size + len(chunk) > limit:
            chunks.append(chunk[: max(0, limit - size)])
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


class DorkLinkCrawler:
    def __init__(self, config: ScanConfig, store: ScanStore, progress, cancelled):
        self.config = config
        self.store = store
        self.progress = progress
        self.cancelled = cancelled
        self.pages_visited = 0
        self.errors = 0
        # File links are keyed by their normalized URL, while page URLs retain their
        # exact query parameters because those can select different page content.
        self.files: dict[str, FileLink] = {}
        self.external_unverified = 0
        self.visited: set[str] = set()
        self.queued: set[str] = set()
        self.gate = _RequestGate(config.request_delay, cancelled)
        self.robots_by_origin: dict[str, _Robots] = {}
        self.public_dns_cache: dict[str, bool] = {}
        self.render_semaphore = asyncio.Semaphore(max(1, min(2, config.concurrency)))

    async def run(self) -> tuple[int, list[FileLink], int]:
        target = normalize_target(self.config.target_url)
        host = urlparse(target).hostname or ""
        started = _utcnow()
        scan_id = self.store.start_scan(target, started)
        timeout = httpx.Timeout(15.0, connect=8.0)
        limits = httpx.Limits(max_connections=max(4, self.config.concurrency * 2), max_keepalive_connections=self.config.concurrency)
        headers = {"User-Agent": self.config.user_agent, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.5"}
        async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers, follow_redirects=False) as client:
            await self._robots_for(client, target)
            provider = GoogleSearchProvider(client, host, self.config.request_delay, self._search_warning)
            secondary_provider = DuckDuckGoSearchProvider(client, host, self.config.request_delay, self._search_warning)
            queries = build_queries(self.config.keywords, self.config.templates, host, self.config.extensions)
            seeds: list[str] = []
            for index, query in enumerate(queries, 1):
                if self.cancelled():
                    break
                self.progress({"phase": "search", "message": f"Search {index}/{len(queries)}: {query}", "pages": self.pages_visited, "files": len(self.files)})
                seeds.extend(await provider.search(query, limit=10))
                seeds.extend(await secondary_provider.search(query, limit=10))
            queue: list[str] = []
            priority_queue: list[str] = []
            direct_seed_files: dict[str, tuple[str, str, str]] = {}
            allowed_extensions = {ext.lower().lstrip(".") for ext in self.config.extensions}
            for url in [target, *seeds]:
                canonical = self._canonical(url)
                if canonical and canonical not in self.queued and safe_http_url(canonical, host):
                    self.queued.add(canonical)
                    extension = _file_extension(canonical)
                    if extension in allowed_extensions:
                        key = file_link_dedupe_key(canonical)
                        direct_seed_files.setdefault(key, (canonical, target, "Search result"))
                    else:
                        queue.append(canonical)

            if direct_seed_files:
                new_items = await self._verify_candidates(client, scan_id, direct_seed_files)
                if new_items:
                    self.store.add_files(scan_id, new_items)
                    self.progress({"phase": "results", "message": f"Found {len(self.files)} file links so far", "pages": self.pages_visited, "files": len(self.files), "new_rows": [item.to_dict() for item in new_items]})
            # Search seeds stay ahead of discovered pages in the breadth-first queue.
            attempts = 0
            while queue and attempts < self.config.max_pages and not self.cancelled():
                batch: list[str] = []
                while queue and len(batch) < self.config.concurrency and attempts + len(batch) < self.config.max_pages:
                    url = queue.pop(0)
                    if url in self.visited or not await self._can_fetch(client, url):
                        continue
                    self.visited.add(url)
                    batch.append(url)
                if not batch:
                    break
                attempts += len(batch)
                for url in batch:
                    self.progress({"phase": "crawl", "message": f"Crawling: {url}", "pages": self.pages_visited, "files": len(self.files)})
                parsed_pages = await asyncio.gather(*(self._fetch_and_parse(client, url, host) for url in batch))
                self.pages_visited += sum(1 for result in parsed_pages if result is not None)
                new_files: dict[str, tuple[str, str, str]] = {}
                new_items: list[FileLink] = []
                external_items: list[FileLink] = []
                for result in parsed_pages:
                    if not result:
                        continue
                    page_url, links = result
                    for candidate in links["pages"]:
                        if candidate not in self.queued and candidate not in self.visited:
                            self.queued.add(candidate)
                            (priority_queue if candidate in links.get("priority_pages", set()) else queue).append(candidate)
                    for file_url, anchor_text in links["files"]:
                        key = file_link_dedupe_key(file_url)
                        existing = self.files.get(key)
                        if existing:
                            if merge_link_evidence(existing, page_url, anchor_text):
                                self.store.update_file_evidence(scan_id, existing)
                            continue
                        if safe_http_url(file_url, host):
                            if await self._can_fetch(client, file_url):
                                if key in new_files:
                                    first_url, first_source, first_anchor = new_files[key]
                                    new_files[key] = (
                                        first_url,
                                        merge_unique_text(first_source, page_url, case_sensitive=True),
                                        merge_unique_text(first_anchor, anchor_text),
                                    )
                                else:
                                    new_files[key] = (file_url, page_url, anchor_text)
                        else:
                            item = self._record_external_file(scan_id, file_url, page_url, anchor_text)
                            if item:
                                external_items.append(item)
                new_items.extend(await self._verify_candidates(client, scan_id, new_files))
                new_items.extend(external_items)
                self.store.add_files(scan_id, new_items)
                self.progress({"phase": "crawl", "message": f"Visited {self.pages_visited} pages; found {len(self.files)} file links", "pages": self.pages_visited, "files": len(self.files), "new_rows": [item.to_dict() for item in new_items]})
                if not queue and priority_queue:
                    queue, priority_queue = priority_queue, []
                elif priority_queue:
                    # Download-labeled landing pages should be crawled before generic navigation.
                    queue = priority_queue + queue
                    priority_queue = []
        finished = _utcnow()
        items = list(self.files.values())
        self.store.finish_scan(scan_id, finished, self.pages_visited, len(items), sum(item.reachable for item in items), self.errors)
        return scan_id, items, self.errors

    async def _load_robots(self, client: httpx.AsyncClient, target: str) -> _Robots:
        robots_url = urljoin(target, "/robots.txt")
        parser = RobotFileParser(robots_url)
        try:
            response = await self._open_scoped(client, "GET", robots_url, urlparse(target).hostname or "", {"Accept": "text/plain"})
            if response is not None:
                async with _response_context(response):
                    if response.status_code < 400:
                        body = await _read_limited(response, MAX_ROBOTS_BYTES)
                        parser.parse(body.decode("utf-8", errors="replace").splitlines())
                    elif response.status_code >= 500 or response.status_code in {401, 403, 429}:
                        self.errors += 1
                        parser.parse(["User-agent: *", "Disallow: /"])
                        self.progress({"phase": "warning", "message": f"robots.txt returned HTTP {response.status_code}; skipping this origin", "pages": self.pages_visited, "files": len(self.files)})
                    else:
                        parser.parse([])
            else:
                self.errors += 1
                parser.parse(["User-agent: *", "Disallow: /"])
                self.progress({"phase": "warning", "message": "robots.txt could not be reached; skipping this origin", "pages": self.pages_visited, "files": len(self.files)})
        except httpx.HTTPError as exc:
            self.errors += 1
            log.info("Could not read robots.txt: %s", exc)
            parser.parse(["User-agent: *", "Disallow: /"])
            self.progress({"phase": "warning", "message": "robots.txt could not be reached; skipping this origin", "pages": self.pages_visited, "files": len(self.files)})
        if not parser.default_entry and not parser.entries:
            parser.parse([])
        try:
            crawl_delay = parser.crawl_delay(self.config.user_agent)
            if crawl_delay is not None:
                self.gate.delay = max(self.gate.delay, float(crawl_delay))
        except (TypeError, ValueError):
            pass
        return _Robots(parser, self.config.user_agent)

    def _search_warning(self, message: str) -> None:
        self.errors += 1
        self.progress({"phase": "warning", "message": message, "pages": self.pages_visited, "files": len(self.files)})

    async def _verify_candidates(self, client: httpx.AsyncClient, scan_id: int, candidates: dict[str, tuple[str, str, str]]) -> list[FileLink]:
        if not candidates:
            return []
        semaphore = asyncio.Semaphore(max(1, self.config.concurrency))

        async def verify(candidate: str, source: str, text: str):
            async with semaphore:
                return await self._verify_file(client, candidate, source, text)

        allowed = {ext.lower().lstrip(".") for ext in self.config.extensions}
        verified = await asyncio.gather(*(verify(url, source, text) for url, source, text in candidates.values()))
        added: list[FileLink] = []
        for item in verified:
            if item.extension and item.extension not in allowed:
                continue
            if not item.extension and not self._is_download_response(item):
                # A link labeled “Download” can lead to an HTML article or a login page.
                # Keep out-of-scope redirect candidates visible, but don't call HTML a file.
                if not (item.error == "Redirect left the target domain" and _has_download_hint(item.anchor_text)):
                    continue
            key = file_link_dedupe_key(item.url)
            existing = self.files.get(key)
            if existing:
                if merge_link_evidence(existing, item.source_page, item.anchor_text):
                    self.store.update_file_evidence(scan_id, existing)
                continue
            self.files[key] = item
            added.append(item)
        return added

    def _record_external_file(self, scan_id: int, url: str, source: str, anchor: str) -> FileLink | None:
        extension = _file_extension(url)
        allowed = {ext.lower().lstrip(".") for ext in self.config.extensions}
        key = file_link_dedupe_key(url)
        if extension not in allowed:
            return None
        existing = self.files.get(key)
        if existing:
            if merge_link_evidence(existing, source, anchor):
                self.store.update_file_evidence(scan_id, existing)
            return None
        item = FileLink(
            url=url,
            source_page=source,
            anchor_text=anchor,
            extension=extension,
            error="External host; not checked (outside target scope)",
        )
        self.files[key] = item
        self.external_unverified += 1
        return item

    @staticmethod
    def _is_download_response(item: FileLink) -> bool:
        if item.extension:
            return True
        content_type = item.content_type.split(";", 1)[0].strip().lower()
        if not content_type or content_type in {"text/html", "application/xhtml+xml", "application/json"}:
            return False
        return item.reachable and (
            content_type == "application/octet-stream"
            or content_type.startswith(("application/", "audio/", "image/", "video/"))
            or content_type.startswith("text/")
        )

    async def _robots_for(self, client: httpx.AsyncClient, url: str) -> _Robots:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        if origin not in self.robots_by_origin:
            self.robots_by_origin[origin] = await self._load_robots(client, origin)
        return self.robots_by_origin[origin]

    async def _can_fetch(self, client: httpx.AsyncClient, url: str) -> bool:
        return (await self._robots_for(client, url)).can_fetch(url)

    async def _fetch_html(self, client: httpx.AsyncClient, url: str) -> tuple[str, str]:
        try:
            target_host = urlparse(normalize_target(self.config.target_url)).hostname or ""
            response = await self._open_scoped(client, "GET", url, target_host, {"Accept": "text/html,application/xhtml+xml"})
            if response is None:
                self.errors += 1
                self.progress({"phase": "warning", "message": f"Skipped unreachable, non-public, or out-of-scope URL: {url}", "pages": self.pages_visited, "files": len(self.files)})
                return "", url
            async with _response_context(response):
                final_url = str(response.url)
                if not await self._can_fetch(client, final_url):
                    return "", final_url
                if response.status_code >= 400:
                    self.errors += 1
                    return "", final_url
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
                    return "", final_url
                body = await _read_limited(response, MAX_HTML_BYTES)
                html = body.decode(response.encoding or "utf-8", errors="replace")
            if self.config.use_playwright:
                parsed = self._parse_page(html, final_url, target_host, self.config.extensions)
                has_direct_file = any(
                    _file_extension(candidate) in {ext.lower().lstrip(".") for ext in self.config.extensions}
                    and safe_http_url(candidate, target_host)
                    for candidate, _label in parsed["files"]
                )
                if not has_direct_file:
                    rendered = await self._render_page(final_url, client)
                    if rendered:
                        html = rendered
            return html, final_url
        except httpx.HTTPError as exc:
            self.errors += 1
            log.info("Page fetch failed %s: %s", url, exc)
            return "", url

    async def _fetch_and_parse(self, client: httpx.AsyncClient, url: str, host: str):
        try:
            html, final_url = await self._fetch_html(client, url)
            if not html:
                return None
            return final_url, self._parse_page(html, final_url, host, self.config.extensions)
        except Exception as exc:
            self.errors += 1
            log.exception("Could not parse page %s", url)
            self.progress({"phase": "warning", "message": f"Could not parse page {url}: {exc}", "pages": self.pages_visited, "files": len(self.files)})
            return None

    def _parse_page(self, html: str, page_url: str, host: str, extensions: tuple[str, ...]) -> dict[str, list]:
        soup = BeautifulSoup(html, "lxml")
        title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        allowed = {ext.lower().lstrip(".") for ext in extensions}
        files: list[tuple[str, str]] = []
        pages: list[str] = []
        priority_pages: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:")):
                continue
            absolute, _fragment = urldefrag(urljoin(page_url, href))
            extension = _file_extension(absolute)
            text = _clean_text(" ".join((
                anchor.get_text(" ", strip=True), anchor.get("aria-label", ""),
                anchor.get("title", ""), " ".join(anchor.get("class", [])),
                " ".join(anchor.get("rel", [])),
            )))
            if not safe_http_url(absolute, host):
                if _is_external_http_url(absolute, host) and extension in allowed:
                    files.append((absolute, text or title))
                continue
            has_download_attr = anchor.has_attr("download")
            download_extension = _file_extension(str(anchor.get("download", ""))) if has_download_attr else ""
            query_keys = {key.lower() for key in parse_qs(urlparse(absolute).query)}
            download_query = bool(query_keys & {"download", "attachment", "export"})
            download_hint = _has_download_hint(text)
            explicit_file = extension in allowed or download_extension in allowed
            extensionless_download = (has_download_attr and not extension and not download_extension) or download_query or (download_hint and _looks_like_download_endpoint(absolute) and not extension)
            if explicit_file:
                files.append((absolute, text or title))
            elif extension in NON_PAGE_EXTENSIONS:
                continue
            else:
                if extensionless_download and (absolute, text or title) not in files:
                    # It may be a direct endpoint or an HTML interstitial. Verify it,
                    # and crawl its response when it is a landing page.
                    files.append((absolute, text or title))
                if download_hint or extensionless_download:
                    priority_pages.add(absolute)
                if absolute not in pages:
                    pages.append(absolute)

        # Some sites keep download URLs on buttons instead of anchors. Extract only
        # explicitly named download/file attributes to avoid treating arbitrary data URLs as links.
        for element in soup.select("[data-download-url], [data-file-url]"):
            raw_url = element.get("data-download-url") or element.get("data-file-url") or ""
            if not raw_url.strip():
                continue
            absolute, _fragment = urldefrag(urljoin(page_url, raw_url.strip()))
            if safe_http_url(absolute, host):
                label = _clean_text(element.get_text(" ", strip=True) or element.get("aria-label", "") or element.get("title", ""))
                if (not allowed or _file_extension(absolute) in allowed or not _file_extension(absolute)) and (absolute, label or title) not in files:
                    files.append((absolute, label or title))
                if not _file_extension(absolute) and absolute not in pages:
                    pages.append(absolute)
                    priority_pages.add(absolute)
            elif _is_external_http_url(absolute, host) and _file_extension(absolute) in allowed:
                label = _clean_text(element.get_text(" ", strip=True) or element.get("aria-label", "") or element.get("title", ""))
                if (absolute, label or title) not in files:
                    files.append((absolute, label or title))
        return {"files": files, "pages": pages, "priority_pages": priority_pages}

    async def _verify_file(self, client: httpx.AsyncClient, url: str, source: str, anchor: str) -> FileLink:
        item = FileLink(url=url, source_page=source, anchor_text=anchor, extension=_file_extension(url))
        try:
            host = urlparse(normalize_target(self.config.target_url)).hostname or ""
            response = await self._open_scoped(client, "HEAD", url, host, {"Accept": "*/*"})
            if response is None:
                item.error = "Redirect left the target domain"
                self.errors += 1
                return item
            if not await self._can_fetch(client, str(response.url)):
                await response.aclose()
                item.error = "Redirect destination disallowed by robots.txt"
                return item
            async with _response_context(response):
                status = response.status_code
                self._set_verification(item, response)
            if status in {403, 405, 501}:
                ranged = await self._open_scoped(client, "GET", url, host, {"Range": "bytes=0-0", "Accept": "*/*"})
                if ranged is not None:
                    if not await self._can_fetch(client, str(ranged.url)):
                        await ranged.aclose()
                        item.reachable = False
                        item.error = "Redirect destination disallowed by robots.txt"
                        return item
                    async with _response_context(ranged):
                        self._set_verification(item, ranged)
        except httpx.HTTPError as exc:
            self.errors += 1
            item.error = str(exc)[:300]
        except Exception as exc:
            self.errors += 1
            item.error = str(exc)[:300]
            log.exception("Could not verify file URL %s", url)
        return item

    @staticmethod
    def _set_verification(item: FileLink, response: httpx.Response) -> None:
        item.http_status = response.status_code
        item.content_type = response.headers.get("content-type", "")[:200]
        if not item.extension:
            disposition = response.headers.get("content-disposition", "")
            match = re.search(r"filename\*?=(?:UTF-8''|\")?([^;\"]+)", disposition, flags=re.IGNORECASE)
            filename = match.group(1).strip().strip('"') if match else ""
            filename_extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower().lstrip(".")
            mime_type = item.content_type.split(";", 1)[0].strip()
            mime_extension = mimetypes.guess_extension(mime_type) if item.content_type and mime_type != "application/octet-stream" else None
            item.extension = filename_extension or (mime_extension.lstrip(".") if mime_extension else "")
        length = response.headers.get("content-length")
        try:
            item.content_length = int(length) if length is not None else None
        except ValueError:
            item.content_length = None
        item.reachable = 200 <= response.status_code < 400
        if not item.reachable:
            item.error = f"HTTP {response.status_code}"

    async def _open_scoped(self, client: httpx.AsyncClient, method: str, url: str, target_host: str, headers: dict[str, str]) -> httpx.Response | None:
        current = url
        for _ in range(6):
            if not safe_http_url(current, target_host) or not await self._is_public_host(current):
                return None
            await self.gate.wait()
            request = client.build_request(method, current, headers=headers)
            if self.cancelled():
                return None
            response = await client.send(request, stream=True, follow_redirects=False)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                return None
            current = urljoin(current, location)
        return None

    async def _is_public_host(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False
        if host in self.public_dns_cache:
            return self.public_dns_cache[host]
        try:
            address = ipaddress.ip_address(host)
            result = address.is_global
        except ValueError:
            try:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                infos = await asyncio.wait_for(
                    asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
                    timeout=5.0,
                )
                addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
                result = bool(addresses) and all(address.is_global for address in addresses)
            except (OSError, ValueError, asyncio.TimeoutError):
                result = False
        self.public_dns_cache[host] = result
        return result

    async def _render_page(self, url: str, client: httpx.AsyncClient) -> str:
        """Optional regular browser rendering for public pages whose links are added by JavaScript."""
        async with self.render_semaphore:
            return await self._render_page_limited(url, client)

    async def _render_page_limited(self, url: str, client: httpx.AsyncClient) -> str:
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(user_agent=self.config.user_agent)
                    host = urlparse(normalize_target(self.config.target_url)).hostname or ""

                    async def restrict_route(route):
                        if (safe_http_url(route.request.url, host)
                                and await self._is_public_host(route.request.url)
                                and await self._can_fetch(client, route.request.url)):
                            await route.continue_()
                        else:
                            await route.abort()

                    await page.route("**/*", restrict_route)
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(800)
                    return await page.content()
                finally:
                    await browser.close()
        except Exception as exc:
            self.errors += 1
            log.info("Optional browser rendering unavailable for %s: %s", url, exc)
            self.progress({"phase": "warning", "message": f"JavaScript rendering unavailable: {exc}", "pages": self.pages_visited, "files": len(self.files)})
            return ""

    @staticmethod
    def _canonical(url: str) -> str:
        try:
            parsed = urlparse(url)
            return parsed._replace(fragment="").geturl() if parsed.scheme in {"http", "https"} and parsed.hostname else ""
        except ValueError:
            return ""
