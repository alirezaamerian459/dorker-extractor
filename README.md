# DorkLink Extractor

DorkLink Extractor is a Windows desktop application for finding and checking publicly accessible file links on a website you are authorized to assess. It combines Google and DuckDuckGo HTML search results, a bounded same-domain crawler, optional JavaScript rendering, SQLite history, and CSV export.

## What it does

- Searches Google and DuckDuckGo for relevant HTML pages on the target domain. The crawler then extracts only the selected file extensions from those pages, including links hosted on external file servers.
- Finds public same-site GET search forms (for example, WordPress `/?s=...`) and seeds them with the entered keywords, so a domain-only target can discover site-native search results without requiring a full page URL.
- Searches each comma-separated or line-separated keyword phrase separately. For link filtering, combined specific phrases such as `Minecraft PS5` take precedence over single-word alternatives; broad search terms such as `download` and `firmware` cannot satisfy the game/platform match. If only separate specific words are entered, they must all appear in each link. Optional **Must include** terms add stricter filters and are combined with AND. Page titles, sidebars, and recommendations do not satisfy the filter.
- Accepts `{keyword}`, `{required}`, `{site}`, `{target}`, and `{ext}` variables in templates. Required terms are added automatically when a custom template omits `{required}`. If no template is provided, it uses `site:{site} {required} {keyword}`.
- Keeps page crawling, redirects, and file-link checks on the target domain and its subdomains. Direct file links on external hosts are listed as unverified and are never contacted.
- Resolves in-scope hosts before requesting them and rejects non-public DNS addresses.
- Reads `robots.txt` for each origin and skips disallowed pages and file URLs. If robots rules cannot be read or the server is rate-limiting or failing, that origin is skipped.
- Applies the site's `Crawl-delay` when present, a configurable request delay and concurrency limit, and a page cap.
- Detects selected file extensions in paths and filename query parameters, recognizes English and Persian download labels, and prioritizes those landing pages. In-scope links are checked with `HEAD` or a one-byte range request; file contents are not saved or downloaded.
- Uses Playwright only when enabled and a fetched page has no matching file links. Browser requests outside the target domain are blocked.
- Stores scan metadata and link results in a local SQLite database. Results appear as batches arrive and the latest saved rows are restored after restarting the app, including rows from an interrupted scan.
- Collapses duplicate file URLs across fragments and common analytics parameters while preserving signed and other functional download parameters. It keeps the distinct source pages and link labels for each collapsed result.
- Separates results into **All**, one tab per file type, and **Other / unknown**. CSV export follows the selected tab; choose **All** to export every unique link.
- Exports to UTF-8 CSV with a BOM for spreadsheet compatibility.
- Includes English and Persian (RTL) interface text.
- Can scan a folder that you explicitly choose and infer game-name candidates from common game-library folders, Steam app manifests, and a broad set of PC, console, ROM, package, archive, and split-file names (including `.pkg`, `.fpkg`, and `.fpk`). Windows-visible NTFS and exFAT volumes use the same read-only scanner. It never scans all drives by default, follows links, unpacks archives, executes files, or changes local files. It does not inspect raw filesystems Windows cannot mount or read package contents.

Google may rate-limit or change its public HTML response. A blocked search request is logged and the crawler can still inspect the target homepage and pages linked from it. The app does not bypass logins, CAPTCHAs, paywalls, or access controls.

Search keywords, templates, and the target domain are sent to Google and DuckDuckGo as search queries. Avoid putting private information in those fields.

## Requirements

- Windows 10 or later
- Python 3.12+
- A network connection for search and public website requests

## Run from source

In PowerShell, from this folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

For the optional JavaScript renderer, install the Playwright Chromium runtime once:

```powershell
python -m playwright install chromium
```

Without that runtime the regular HTTP crawler still works; a rendering warning appears in the activity log if the option is selected.

## Use

1. Enter only the website domain you are authorized to assess, such as `p30day.ir`. Page paths are ignored. Private, loopback, and link-local IP targets are rejected.
2. Under **Keywords**, enter the game name and platform together as one phrase, such as `Minecraft PS5`. Every word must match the specific file link or filename; broad terms such as `download` do not satisfy the filter, and version numbers are not needed. Separate genuinely alternative game/platform phrases with commas or new lines.
3. Leave **Must include** empty to find every version. Use it only for extra precision when you want every supplied term to appear in the link label, URL, or filename.
   - To use games already on your disk, select **Find game names in a local folder…**, choose a folder such as a Steam library or game archive folder, review the candidates, and add up to eight selected names as keywords. Names are guesses from local metadata and do not prove ownership.
4. Add one search template per line, or leave the field empty to use the default. Search for the article pages; the selected file extensions are filtered after crawling. Example:

   ```text
   site:{site} {required} {keyword}
   ```

5. The default search list is limited to `.rar`, `.exfat`, `.fpkg`, and `.pkg`. Using `{ext}` runs one search per selected extension. Direct file links hosted on another domain appear as **Not checked** because the scanner stays within the target website.
6. Confirm authorization and start the scan.
7. Choose **All** or a file-type tab, then select **Export CSV**. The selected tab determines which links are included.

The required-term filter checks page identity and link metadata only. It cannot inspect the downloaded package or prove which platform/version the file contains. A generic title and generic download button may be filtered out even when the page body mentions the required terms.

The SQLite database is created in the current Windows user's application data directory under `DorkLink\Extractor\dorklink.sqlite3`.

## Build a Windows executable

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean DorkLinkExtractor.spec
```

The executable is written to `dist\DorkLinkExtractor\`. Playwright's browser runtime is not bundled; install it separately if JavaScript rendering is needed.

## Development

Install the optional development dependency and run the offline unit tests:

```powershell
pip install -e ".[dev]"
pytest
```

The tests cover domain normalization, template expansion, same-domain and external-link extraction, and CSV output. They do not make network requests.
