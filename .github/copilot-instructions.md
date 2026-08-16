# Copilot Instructions

## Tooling

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management and [`just`](https://just.systems/) as a command runner.

| Command         | Description                                                |
|-----------------|------------------------------------------------------------|
| `just sync`     | Install / sync dependencies                                |
| `just run`      | Run the scraper (requires Firefox + geckodriver on PATH)   |
| `just check`    | Run all static-analysis checks (lint + fmt + typecheck)    |
| `just lint`     | Ruff lint (report only)                                    |
| `just fix`      | Auto-fix lint issues and format with Ruff                  |
| `just typecheck`| Type-check with `ty`                                       |
| `just pysentry` | Dependency security scan with `pysentry-rs`                |
| `just clean`    | Remove generated `.xlsx` and `error_log.txt`               |
| `just update`   | Upgrade all dependencies                                   |

Dependencies are declared in `pyproject.toml`; the lockfile is `uv.lock`. Requires Python ≥ 3.11.
Dev dependencies (`ruff`, `ty`) are in `[dependency-groups] dev` and installed automatically by `uv sync`.

**Ruff config** (`pyproject.toml`): `line-length = 100`, rule sets `E`, `F`, `W`, `I`.
**ty** resolves types against `.venv`.

> Note: The README lists `requests` as a dependency but it is not used. `selenium` and `openpyxl` are the actual additional requirements.

## Architecture

The entire scraper lives in a single file: `auto_trader.py`. The entry point initialises a global Firefox `driver` and `WebDriverWait` instance, then calls `_load_extras_prices()` and `get_config()`.

**Data flow:**
1. `_load_extras_prices()` — loads `extras_prices.json` into the module-level `EXTRAS_PRICES: dict[str, int]` and `EXTRA_COLUMNS: list[str]` constants at startup.
2. `get_config(autotrader_config.csv)` — reads search parameters (postcode, start page, year range, radius), then iterates every make from `make.json`.
3. `get_total_pages()` — navigates to autotrader.co.uk, determines total pages (capped at 101), then loops pages scraping listings in **two passes**:
   - **Pass 1** (search page): `get_title()`, `get_price()`, `get_other_car_data()` parse the BeautifulSoup tree for each listing `<li>`. The detail URL is extracted from the title anchor `href`.
   - **Pass 2** (detail pages): for each listing, `get_extras(detail_url)` navigates to the individual car page, clicks "View all specs and features", expands the "Added extras" accordion, and matches the extras text against `EXTRAS_PRICES`.
4. `save_data()` — appends to a timestamped `.xlsx` file (created once per run by `get_file_name()`), deduplicates on `[Title, Price, Year, Miles, Fuel_Type]`, drops rows with missing Title or Price, and ensures all extra columns are present.

**Output:** `autotrader_info_YYYY-MM-DD_HH-MM-SS.xlsx` in the working directory.
**Errors:** Appended to `error_log.txt` (page-level exceptions are caught and logged; execution continues).
**Structured log:** `scraper.log` — DEBUG level (full selector traces, HTML snippets, row counts). Console shows INFO+.

## Spreadsheet Column Layout

| Columns | Description |
|---|---|
| `Title`, `Price`, `Year`, `Miles`, `Fuel_Type` | Core search-page data (always present) |
| 38 extras columns (e.g. `Panoramic Roof Fixed Glass`) | GBP list price when the extra is present on the car; `0` when absent. Column names are the canonical keys from `extras_prices.json`. |
| `Extras_Total` | Integer sum of all extra-column values for that row. |
| `Extras_Other` | Comma-separated string of extras found on the car but not matched to the price table. |

## Key Files

- **`extras_prices.json`** — 38 canonical extra name → integer GBP list price entries. Keys are used verbatim as spreadsheet column headers. Add or rename entries here to change what is tracked; no code changes required.
- **`make.json`** — drives which cars are searched. Each entry: `{"displayName", "uriValue", "model", "fuelType", "aggregatedTrim"}`. Adding new searches = adding entries here.
- **`autotrader_config.csv`** — search location/date range. Columns: `PostalCode`, `PageNumber` (resumable start page), `year-from`, `year-to`, `radius`.
- **`specs/extras.md`** — implementation plan for the extras feature.

## Key Conventions

- **Global driver/wait**: `driver: webdriver.Firefox` and `wait: WebDriverWait` are module-level declarations (with `# type: ignore[assignment]`) set in `main()` and used directly inside functions — not passed as arguments.
- **EXTRAS_PRICES / EXTRA_COLUMNS**: module-level dicts/lists populated by `_load_extras_prices()` before `main()` starts. Functions read them as globals — no need to pass them as arguments.
- **make.json iteration order**: Makes are loaded and reversed (`[::-1]`) before iteration. Preserve this when modifying make loading.
- **Pagination cap**: Total pages are capped at `min(101, actual_pages)` — AutoTrader limits search results to 100 pages.
- **Listing selector**: `soup.select('a[data-testid="search-listing-title"]')` — finds only fully-loaded cards. The page is fully scrolled before parsing via `scroll_to_load_all()`. A 2 s sleep after the explicit wait gives React time to hydrate the infinite-scroll container before the first height measurement.
- **Two-pass scraping**: Pass 1 collects all search-page data into `raw_listings` (with `_detail_url` key). Pass 2 pops `_detail_url` from each row and visits the detail page. The browser is on the last detail page after Pass 2; the outer loop navigates directly to the next search page URL.
- **Price selector**: `[data-testid*="price"]` (preferred, stable) with a fallback to any text node matching `£\d+`. The old auto-generated CSS class `span.at__sc-1mc7cl3-5.edXwbj` is gone.
- **Extras matching**: `_normalise()` lowercases and strips punctuation; `_match_extra()` tries exact then keyword match (all words ≥ 4 chars from canonical name must appear in the extra text).
- **Detail page navigation**: `get_extras()` uses `WebDriverWait(driver, 60)` for page load, then tries multiple `data-testid` selectors and XPath text searches to click "View all specs and features" and "Added extras". If either click fails, it returns `{}` and logs at DEBUG.
- **Cookie consent**: `setup_cookies()` sets `acceptATCookies=true` and `atwv=1` on `.autotrader.co.uk` before any search. Without these the Sourcepoint CMP blocks listings from rendering.
- **Page load strategy**: `eager` (fires on DOMContentLoaded, not `load`) — prevents hanging on AutoTrader's ad/tracking resources.
