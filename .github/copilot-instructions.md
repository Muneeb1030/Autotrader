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

The entire scraper lives in a single file: `auto_trader.py`. The entry point initialises a global Firefox `driver` and `WebDriverWait` instance, then calls `get_config()`.

**Data flow:**
1. `get_config(autotrader_config.csv)` — reads search parameters (postcode, start page, year range), then iterates every make from `make.json` × every fuel type in `fuel_types_list`.
2. `get_total_pages()` — navigates to autotrader.co.uk, determines total pages (capped at 101), then loops pages scraping listings.
3. Each page: `get_title()`, `get_price()`, `get_other_car_data()` parse the BeautifulSoup tree for each listing `<li>`.
4. `save_data()` — appends to a timestamped `.xlsx` file (created once per run by `get_file_name()`), deduplicates on `[Title, Price, Year, Miles, Fuel_Type]`, and drops rows with any `NaN`.

**Output:** `autotrader_info_YYYY-MM-DD_HH-MM-SS.xlsx` in the working directory.
**Errors:** Appended to `error_log.txt` (page-level exceptions are caught and logged; execution continues).
**Structured log:** `scraper.log` — DEBUG level (full selector traces, HTML snippets, row counts). Console shows INFO+.

## Key Conventions

- **Global driver/wait**: `driver` and `wait` are module-level globals set in `__main__` and used directly inside functions — not passed as arguments.
- **make.json iteration order**: Makes are loaded and reversed (`[::-1]`) before iteration. Preserve this when modifying make loading.
- **Pagination cap**: Total pages are capped at `min(101, actual_pages)` — AutoTrader limits search results to 100 pages.
- **Listing selector**: `soup.select('ul[data-testid="desktop-search"] > li')[1:]` — skips index 0 (non-listing element). The page is fully scrolled before parsing via `scroll_to_load_all()` to trigger lazy-loading of all results.
- **Price selector**: `span.at__sc-1mc7cl3-5.edXwbj` — this is an auto-generated CSS class name that is likely to change when AutoTrader redeploys. This is the most fragile part of the scraper.
- **Config file path**: Hardcoded at the top of `auto_trader.py` as `enter_path_of_config_file = r"autotrader_config.csv"`. The script must be run from the repo root, or this path updated.
- **CSV config columns**: `PostalCode`, `PageNumber` (resumable start page), `year-from`, `year-to`.
