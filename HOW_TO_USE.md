# How to use DorkLink Extractor

DorkLink Extractor finds public file links on a website that you are authorized to assess. It searches for matching article pages, follows a bounded number of public pages, identifies the selected file formats, and verifies in-scope links. It does not log in, bypass CAPTCHAs, download files, or inspect package contents.

## Install from source

Use Python 3.12 or newer in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

The optional JavaScript renderer needs Chromium once:

```powershell
python -m playwright install chromium
```

Without Chromium, normal HTTP crawling still works. The app reports a rendering warning and continues with the page HTML it could fetch.

## Desktop app

Start the desktop interface from the repository folder:

```powershell
python main.py
```

If you built the launcher, run `dist\DorkLinkExtractorLauncher.exe`. The form contains these fields:

1. **Target website**: enter a domain such as `p30day.ir`. Use only a public website you are authorized to scan.
2. **Keywords**: enter one phrase per line. Use `PPSA17221` for an exact PlayStation title ID, or use a phrase such as `Minecraft PS5`. Separate genuine alternatives with new lines.
3. **Must include**: leave empty unless every result must contain additional terms.
4. **Search templates**: leave the default unless you need a custom query. The variables are `{site}`, `{target}`, `{keyword}`, `{required}`, and `{ext}`.
5. **File extensions**: comma-separated formats. The default is `rar, exfat, fpkg, pkg`.
6. **Maximum pages**, **Concurrency**, and **Delay**: use the defaults first. Increase the page limit when the target site has a large sitemap.
7. **Render pages with JavaScript**: enable this when downloads are inserted by JavaScript. Install Chromium first for full rendering.
8. Tick the authorization checkbox and select **Start scan**.

Results appear in **All** and file-type tabs. Select **All** to export every result with **Export CSV**. A link hosted on a different file server is shown as **Not checked** because the crawler does not contact external hosts; the URL is still listed when it matches the page title or title ID.

### Exact title-ID example

To search for the two known public PPSA17221 links:

- Target website: `p30day.ir`
- Keywords: `PPSA17221`
- File extensions: `rar, exfat, fpkg, pkg`
- JavaScript rendering: optional, but enable it when the site requires it

The crawler first checks the target homepage, then prioritizes the site's own search page (for example `?s=PPSA17221`) before spending the page budget on general sitemap pages. A title ID can match the article heading even when the download button only says `Download`.

## Command-line interface

After `pip install -e .`, use either the installed command or the module form:

```powershell
dorklink-cli p30day.ir PPSA17221 --render-js
python -m dorklink.cli p30day.ir PPSA17221 --render-js
```

URLs go to standard output and progress goes to standard error. Useful options:

```powershell
# Structured results
dorklink-cli p30day.ir PPSA17221 --format json

# Spreadsheet output
dorklink-cli p30day.ir PPSA17221 --format csv --output results.csv

# Limit formats and pages for a quick scan
dorklink-cli p30day.ir PPSA17221 --extensions rar,pkg --max-pages 10 --delay 0.5

# Add a required term and keep a reusable SQLite history
dorklink-cli p30day.ir PPSA17221 --required-term Minecraft --database scans.sqlite3

# Use more than one custom search template
dorklink-cli p30day.ir PPSA17221 \
  --template 'site:{site} {required} {keyword}' \
  --template 'site:{site} intitle:index.of {keyword}'
```

Run `dorklink-cli --help` to see every option. The default output is one matching URL per line. JSON includes the scan ID, pages visited, error count, and full link metadata. CSV includes the same fields used by the desktop export.

## Understanding results

- **Reachable** means the crawler received a successful response for an in-scope URL.
- **Not checked** means the file is on an external host. The URL is listed for discovery, but the crawler intentionally does not request it.
- **Errors** often include search-engine rate limits, blocked robots rules, unavailable pages, or an optional browser runtime that is not installed. Read the Activity log or CLI standard error for details.
- The database stores scan metadata and rows. The desktop app uses its application-data database; the CLI uses a temporary database unless `--database` is supplied.

## If a search returns zero links

Check the spelling of the domain and title ID, keep the title ID as a single keyword, include the file extension used by the site, and raise **Maximum pages** if the site is large. Review the Activity log to confirm that the site's own search page was visited. For JavaScript-heavy pages, install Chromium and retry with rendering enabled. Search results depend on pages being public and reachable at scan time.

## Responsible use

Only scan public pages and domains you are authorized to assess. The crawler stays within the target domain for page requests, follows robots rules, applies a request delay, and keeps external file links unverified.
