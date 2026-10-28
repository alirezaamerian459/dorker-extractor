from pathlib import Path

import pytest

from dorklink.crawler import DorkLinkCrawler, _file_extension
from dorklink.export import export_csv
from dorklink.models import FileLink, ScanConfig
from dorklink.local_games import normalize_game_name, scan_local_game_names
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


def test_title_id_search_adds_unqualified_page_query():
    queries = build_queries(("PPSA17221",), ("site:{site} {keyword} filetype:{ext}",), "example.org", ("pkg", "rar"))

    assert queries == [
        "site:example.org PPSA17221 filetype:pkg",
        "site:example.org PPSA17221 filetype:rar",
        "site:example.org PPSA17221",
    ]


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


def test_query_filename_extension_and_download_landing_page_priority(tmp_path: Path):
    assert _file_extension("https://example.org/get.php?filename=annual%20report.pdf") == "pdf"
    assert _file_extension("https://example.org/download.php?id=42") == ""

    crawler = DorkLinkCrawler(
        ScanConfig("https://example.org", (), (), ("pdf",)),
        ScanStore(tmp_path / "test.sqlite3"),
        lambda _: None,
        lambda: False,
    )
    parsed = crawler._parse_page(
        '<a href="/articles/game">دانلود بازی</a>'
        '<a href="/download.php?id=42">Download file</a>',
        "https://example.org/",
        "example.org",
        ("pdf",),
    )

    assert "https://example.org/articles/game" in parsed["priority_pages"]
    assert "https://example.org/articles/game" in parsed["pages"]
    assert "https://example.org/download.php?id=42" in parsed["priority_pages"]
    assert ("https://example.org/download.php?id=42", "Download file") in parsed["files"]


@pytest.mark.parametrize(
    "html",
    [
        '<title>Minecraft PPSA17221</title><a href="/files/game.pkg">Download</a>',
        '<h1>Minecraft PPSA17221</h1><div><a href="/files/game.pkg">Download</a></div>',
    ],
)
def test_title_id_matches_page_identity_for_generic_download_links(tmp_path: Path, html: str):
    crawler = DorkLinkCrawler(
        ScanConfig("https://example.org", ("PPSA17221",), (), ("pkg",)),
        ScanStore(tmp_path / "title-id.sqlite3"),
        lambda _: None,
        lambda: False,
    )

    parsed = crawler._parse_page(html, "https://example.org/", "example.org", ("pkg",))

    assert parsed["files"] == [("https://example.org/files/game.pkg", "Download")]


def test_preliminary_parse_does_not_consume_site_search_seed(tmp_path: Path):
    crawler = DorkLinkCrawler(
        ScanConfig("https://example.org", ("PPSA17221",), (), ("pkg",)),
        ScanStore(tmp_path / "search-seed.sqlite3"),
        lambda _: None,
        lambda: False,
    )
    html = '<form method="get" action="/"><input type="search" name="s"></form>'

    preliminary = crawler._parse_page(
        html,
        "https://example.org/",
        "example.org",
        ("pkg",),
        collect_site_search_seeds=False,
    )

    assert preliminary["pages"] == []
    assert crawler.site_search_seeds == set()
    definitive = crawler._parse_page(html, "https://example.org/", "example.org", ("pkg",))
    assert definitive["pages"] == ["https://example.org/?s=PPSA17221"]


def test_external_file_links_are_listed_without_becoming_crawl_pages(tmp_path: Path):
    crawler = DorkLinkCrawler(
        ScanConfig("https://example.org", (), (), ("rar",)),
        ScanStore(tmp_path / "test.sqlite3"),
        lambda _: None,
        lambda: False,
    )
    parsed = crawler._parse_page(
        '<a href="https://files.example.net/packages/game.rar">دانلود بازی</a>',
        "https://example.org/downloads/",
        "example.org",
        ("rar",),
    )

    assert parsed["files"] == [("https://files.example.net/packages/game.rar", "دانلود بازی")]
    assert parsed["pages"] == []

    scan_id = crawler.store.start_scan("https://example.org/", "2026-10-24T00:00:00+00:00")
    url, anchor = parsed["files"][0]
    crawler._record_external_file(scan_id, url, "https://example.org/downloads/", anchor)
    stored = crawler.store.rows(scan_id)
    assert len(stored) == 1
    assert stored[0]["error"] == "External host; not checked (outside target scope)"


def test_local_game_name_scan_uses_folder_names_and_steam_manifests(tmp_path: Path):
    root = tmp_path / "Selected Games"
    steamapps = root / "SteamLibrary" / "steamapps"
    game_folder = steamapps / "common" / "Example Game"
    game_folder.mkdir(parents=True)
    (game_folder / "Example Game.exe").write_bytes(b"not executed")
    (steamapps / "appmanifest_123.acf").write_text(
        '"AppState" { "name" "Example Game" "installdir" "Example Game" }',
        encoding="utf-8",
    )
    packages = root / "PS4 Packages"
    packages.mkdir()
    (packages / "Another_Game [CUSA12345] v1.2.pkg").write_bytes(b"metadata only")

    assert normalize_game_name("Another_Game [CUSA12345] v1.2.pkg") == "Another Game"
    result = scan_local_game_names(root)
    by_name = {candidate.name: candidate for candidate in result.candidates}

    assert set(by_name) == {"Another Game", "Example Game"}
    assert by_name["Example Game"].evidence == "Steam manifest"
    assert result.errors == 0


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Example Game.pkg", "Example Game"),
        ("Example Game.fpkg", "Example Game"),
        ("Example Game.fpk", "Example Game"),
        ("Example Game.pkg.001", "Example Game"),
        ("Example Game.iso.002", "Example Game"),
        ("Example Game.part01.rar", "Example Game"),
        ("Example Game.r00", "Example Game"),
        ("Example Game.z01", "Example Game"),
        ("Example Game.nsp", "Example Game"),
        ("Example Game.xci", "Example Game"),
        ("Example Game.appx", "Example Game"),
    ],
)
def test_local_name_parser_handles_game_package_and_split_formats(filename: str, expected: str):
    assert normalize_game_name(filename) == expected


def test_unknown_large_game_data_uses_its_folder_name(tmp_path: Path, monkeypatch):
    from dorklink import local_games

    monkeypatch.setattr(local_games, "UNKNOWN_GAME_DATA_MIN_BYTES", 8)
    game_folder = tmp_path / "Mystery Game"
    game_folder.mkdir()
    (game_folder / "payload.customfmt").write_bytes(b"01234567")

    result = scan_local_game_names(tmp_path)
    assert "Mystery Game" in {candidate.name for candidate in result.candidates}


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
