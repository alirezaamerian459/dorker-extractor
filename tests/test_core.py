from pathlib import Path

import pytest

from dorklink.crawler import DorkLinkCrawler
from dorklink.export import export_csv
from dorklink.models import FileLink, ScanConfig
from dorklink.scope import ScopeError, host_in_scope, normalize_target
from dorklink.search import build_queries
from dorklink.storage import ScanStore


def test_normalize_target_and_domain_scope():
    assert normalize_target("Example.ORG/path") == "https://example.org/"
    assert host_in_scope("https://docs.example.org/a", "example.org")
    assert not host_in_scope("https://notexample.org/a", "example.org")


@pytest.mark.parametrize("target", ["localhost", "127.0.0.1", "192.168.1.4", "http://user:pass@example.org", "https://example.org:bad"])
def test_rejects_unsafe_or_malformed_targets(target):
    with pytest.raises(ScopeError):
        normalize_target(target)


def test_build_queries_expands_variables_and_adds_site_scope():
    queries = build_queries(("climate report",), ("site:{site} {keyword} filetype:{ext}",), "example.org", "pdf")
    assert queries == ["site:example.org climate report filetype:pdf"]
    plain = build_queries(("budget",), ("annual report",), "example.org")
    assert plain == ["site:example.org annual report"]
    multiple = build_queries(("budget",), ("site:{site} filetype:{ext} {keyword}",), "example.org", ("pdf", "docx"))
    assert multiple == ["site:example.org filetype:pdf budget", "site:example.org filetype:docx budget"]


def test_parse_page_extracts_only_selected_file_extensions(tmp_path: Path):
    config = ScanConfig("https://example.org", ("report",), (), ("pdf", "docx"))
    crawler = DorkLinkCrawler(config, ScanStore(tmp_path / "test.sqlite3"), lambda _: None, lambda: False)
    parsed = crawler._parse_page(
        '<title>Reports</title><a href="/files/a.pdf">Annual report</a>'
        '<a href="https://other.org/b.pdf">External</a><a href="/notes.txt">Note</a>'
        '<a href="/files/run.exe">Executable</a><a href="/about">About</a>',
        "https://example.org/",
        "example.org",
        ("pdf", "docx"),
    )
    assert parsed["files"] == [("https://example.org/files/a.pdf", "Annual report")]
    assert "https://example.org/about" in parsed["pages"]
    assert "https://example.org/files/run.exe" not in parsed["pages"]


def test_export_csv_is_utf8_and_keeps_result_fields(tmp_path: Path):
    path = tmp_path / "results.csv"
    export_csv(path, [FileLink(url="https://example.org/a.pdf", source_page="https://example.org/", anchor_text="گزارش", extension="pdf", reachable=True)])
    content = path.read_text(encoding="utf-8-sig")
    assert "url,source_page" in content
    assert "گزارش" in content


def test_sqlite_persists_scan_and_file_link(tmp_path: Path):
    store = ScanStore(tmp_path / "history.sqlite3")
    scan_id = store.start_scan("https://example.org/", "2026-10-24T00:00:00+00:00")
    store.add_file(scan_id, FileLink(url="https://example.org/a.pdf", source_page="https://example.org/", extension="pdf", reachable=True))
    store.finish_scan(scan_id, "2026-10-24T00:00:01+00:00", 1, 1, 1, 0)
    rows = store.rows(scan_id)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.org/a.pdf"
    assert rows[0]["reachable"] == 1
