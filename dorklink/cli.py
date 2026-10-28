from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path

from .crawler import DorkLinkCrawler
from .export import export_csv
from .models import FileLink, ScanConfig
from .storage import ScanStore


DEFAULT_TEMPLATE = "site:{site} {required} {keyword}"
DEFAULT_EXTENSIONS = ("rar", "exfat", "fpkg", "pkg")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dorklink-cli",
        description="Find and verify public file links on an authorized website.",
    )
    parser.add_argument("target", help="Target website, for example p30day.ir")
    parser.add_argument("keywords", nargs="+", help="Keywords or title IDs to search for")
    parser.add_argument(
        "-t", "--template", action="append", dest="templates",
        help="Search template; repeat for multiple templates (default: %(default)s)",
        default=None, metavar="TEMPLATE",
    )
    parser.add_argument(
        "-e", "--extensions", default=",".join(DEFAULT_EXTENSIONS), metavar="LIST",
        help="Comma-separated file extensions (default: %(default)s)",
    )
    parser.add_argument(
        "--required-term", action="append", default=[], metavar="TERM",
        help="Additional term every matching link must contain; repeat as needed",
    )
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum pages to crawl (default: %(default)s)")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent requests (default: %(default)s)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (default: %(default)s)")
    parser.add_argument(
        "--render-js", action="store_true",
        help="Render pages with JavaScript when needed (requires a Playwright browser)",
    )
    parser.add_argument(
        "--format", choices=("text", "json", "csv"), default="text",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument("-o", "--output", type=Path, help="Write results to a file instead of standard output")
    parser.add_argument(
        "--database", type=Path,
        help="SQLite scan history path (default: a temporary database)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress crawl progress")
    return parser


def _csv_list(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().lower().lstrip(".") for item in value.replace(";", ",").split(",") if item.strip()))


def _write_progress(message: str) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        encoded = message.encode(sys.stderr.encoding or "utf-8", errors="replace").decode(sys.stderr.encoding or "utf-8", errors="replace")
        print(encoded, file=sys.stderr, flush=True)


def _write_stdout(value: str) -> None:
    try:
        sys.stdout.write(value)
        sys.stdout.flush()
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write(value.encode("utf-8"))
        buffer.flush()


def _rows_as_dicts(items: list[FileLink]) -> list[dict]:
    return [item.to_dict() for item in items]


def _render_output(items: list[FileLink], scan_id: int, pages: int, errors: int, output_format: str) -> str:
    rows = _rows_as_dicts(items)
    if output_format == "json":
        return json.dumps(
            {"scan_id": scan_id, "pages_visited": pages, "files_found": len(items), "errors": errors, "links": rows},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    if output_format == "csv":
        import io
        buffer = io.StringIO(newline="")
        import csv
        from .export import CSV_FIELDS
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()
    return "".join(f"{item.url}\n" for item in items)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_pages < 1 or args.concurrency < 1 or args.delay < 0:
        _parser().error("--max-pages and --concurrency must be positive; --delay cannot be negative")

    extensions = _csv_list(args.extensions)
    if not extensions:
        _parser().error("at least one file extension is required")
    templates = tuple(args.templates or (DEFAULT_TEMPLATE,))
    required_terms = tuple(dict.fromkeys(term.strip() for term in args.required_term if term.strip()))
    config = ScanConfig(
        target_url=args.target,
        keywords=tuple(dict.fromkeys(keyword.strip() for keyword in args.keywords if keyword.strip())),
        templates=templates,
        extensions=extensions,
        required_terms=required_terms,
        max_pages=args.max_pages,
        concurrency=args.concurrency,
        request_delay=args.delay,
        use_playwright=args.render_js,
    )
    cancelled = threading.Event()
    progress = (lambda event: None) if args.quiet else lambda event: _write_progress(str(event.get("message", "")))

    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.database:
            database = args.database
            database.parent.mkdir(parents=True, exist_ok=True)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="dorklink-cli-")
            database = Path(temporary.name) / "scan.sqlite3"
        try:
            crawler = DorkLinkCrawler(config, ScanStore(database), progress, cancelled.is_set)
            scan_id, items, errors = asyncio.run(crawler.run())
        except KeyboardInterrupt:
            cancelled.set()
            _write_progress("Scan cancelled.")
            return 130

        rendered = _render_output(items, scan_id, crawler.pages_visited, errors, args.format)
        if args.output:
            if args.format == "csv":
                export_csv(args.output, items)
            else:
                args.output.write_text(rendered, encoding="utf-8")
        else:
            _write_stdout(rendered)
        _write_progress(f"Scan #{scan_id} finished: {len(items)} file links, {errors} errors.")
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
