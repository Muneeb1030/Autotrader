# AutoTrader UK Web Scraper

A scraper for [autotrader.co.uk](https://www.autotrader.co.uk) that searches for car listingsand extracts details by viewing each car's individual advert page. It also collect optional extras, and ouputs results to a timestamped `.xlsx` spreadsheet.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](http://creativecommons.org/licenses/by-nc/4.0/)

---

## Features

- Configurable searches by postcode, year range, radius, make, model, trim, fuel type, and maximum price
- Infinite-scroll page loading — all listings on each results page are fully loaded before parsing
- Per-listing detail-page scraping — visits each advert to extract the "Added extras" section
- Extras matched against a per-model price table (`extras_prices.json`) and totalled in a dedicated column
- Structured logging to console (`INFO+`) and `scraper.log` (`DEBUG`)
- Deduplication and `NaN`-dropping on save; output is appended across pages within a single run

---

## Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| [Python ≥ 3.11](https://www.python.org/downloads/) | Runtime | system / pyenv |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Dependency & venv management | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Firefox](https://www.mozilla.org/en-GB/firefox/) | Browser for Selenium | system |
| [geckodriver](https://github.com/mozilla/geckodriver/releases) | Firefox WebDriver bridge | must be on `PATH` |
| [just](https://just.systems/man/en/packages.html) *(optional)* | Command runner | `brew install just` / `cargo install just` |
| [prek](https://prek.j178.dev) *(optional)* | Git pre-commit hooks | `cargo install prek` |
| [betterleaks](https://github.com/crnvl96/betterleaks) *(optional)* | Secret scanning in pre-commit | `cargo install betterleaks` |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Ben-Newton_syne/Autotrader_Web_Scapper.git
cd Autotrader_Web_Scapper

# 2. Install Python dependencies (creates .venv automatically)
just sync

# 3. Install git pre-commit hooks
prek install
```

> `just sync` runs `uv sync` which reads `pyproject.toml`, resolves the lockfile (`uv.lock`), and creates `.venv` — no manual `pip install` required.

---

## Configuration

**Two files must be edited before the first run.** A third controls extras pricing.

### `autotrader_config.csv`

Defines the postal search area and year filter. Each row triggers a separate search iteration.

```csv
PostalCode,PageNumber,year-from,year-to,radius
KT70YJ,1,2020,2022,100
```

| Column | Description |
|---|---|
| `PostalCode` | UK postcode used as the search centre (no spaces) |
| `PageNumber` | Results page to start from — use `1` to start from the beginning |
| `year-from` | Minimum registration year |
| `year-to` | Maximum registration year |
| `radius` | Search radius in miles |

### `make.json`

Defines which make/model combinations to scrape. Each entry produces an independent search. The list is processed in reverse order.

```json
[
    {
        "displayName": "Porsche",
        "uriValue": "Porsche",
        "model": "Taycan",
        "fuelType": "Electric",
        "aggregatedTrim": "4S",
        "price-to": 50000
    }
]
```

| Field | Required | Description |
|---|---|---|
| `uriValue` | ✅ | Make name as used in the AutoTrader URL (e.g. `"Porsche"`) |
| `model` | ✅ | Model name as used in the AutoTrader URL (e.g. `"Taycan"`) |
| `fuelType` | ✅ | Fuel type filter (e.g. `"Electric"`, `"Petrol"`, `"Diesel"`) |
| `aggregatedTrim` | optional | Trim variant (e.g. `"4S"`) — omit to match all trims |
| `price-to` | optional | Maximum asking price in GBP — omit for no upper limit |
| `displayName` | optional | Human-readable label used in log output only |

To search for multiple cars, append additional objects to the array:

```json
[
    { "uriValue": "Porsche", "model": "Taycan",   "fuelType": "Electric", "aggregatedTrim": "4S", "price-to": 50000 },
    { "uriValue": "Hyundai", "model": "Ioniq 5",  "fuelType": "Electric" }
]
```

### `extras_prices.json`

Maps optional extras to their GBP list prices, grouped by `"{Make}_{Model}"` key. When the scraper visits a car's advert page it matches the "Added extras" section against this table. Matched items get individual columns with the list price; unmatched items are captured in `Extras_Other`.

```json
{
    "Porsche_Taycan": {
        "Panoramic Roof Fixed Glass": 1137,
        "Head-Up Display": 1128,
        "Burmester High-End 3D Surround Sound System": 4200
    },
    "Hyundai_Ioniq_5": {
        "Heat Pump": 1000,
        "Digital Side Mirrors": 600
    }
}
```

The key format is `"{uriValue}_{model}"` with spaces replaced by underscores. If no segment exists for a searched model the scraper continues normally — extras columns will be `0` and a warning is logged.

---

## Running

```bash
just run
```

Firefox will open automatically. The scraper logs progress to the console and writes full debug output to `scraper.log`. Output is written to a timestamped file:

```
autotrader_info_YYYY-MM-DD_HH-MM-SS.xlsx
```

---

## Available Commands

| Command | Description |
|---|---|
| `just sync` | Install / sync dependencies via `uv` |
| `just run` | Run the scraper (requires Firefox + geckodriver on `PATH`) |
| `just check` | Run all static-analysis checks (lint + format + typecheck) |
| `just lint` | Ruff lint — report only, no changes |
| `just fix` | Auto-fix lint issues and reformat with Ruff |
| `just typecheck` | Type-check with `ty` |
| `just pysentry` | Dependency security scan with `pysentry-rs` |
| `just clean` | Remove generated `.xlsx` files and `scraper.log` |
| `just update` | Upgrade all dependencies to latest compatible versions |

---

## Output Spreadsheet Columns

| Column | Description |
|---|---|
| `Title` | Car title from the listing |
| `Price` | Asking price (£) |
| `Extras_Total` | Sum of matched optional extras list prices (£) |
| `Year` | Registration year |
| `Miles` | Mileage |
| `Fuel_Type` | Fuel type |
| `Detail_URL` | Direct link to the AutoTrader advert page |
| *(one column per extra)* | GBP list price if the extra is present on this car, otherwise `0` |
| `Extras_Other` | Comma-separated extras found on the car but absent from `extras_prices.json` |

---

## Project Structure

```
Autotrader_Web_Scapper/
├── auto_trader.py          # Entire scraper — all logic in one file
├── make.json               # ✏️ Edit: search targets (make, model, fuel, trim, price ceiling)
├── extras_prices.json      # ✏️ Edit: per-model optional extras price tables
├── autotrader_config.csv   # ✏️ Edit: postcode, year range, radius, start page
├── pyproject.toml          # Python project metadata and dependencies (uv)
├── Justfile                # Task runner commands
├── prek.toml               # Pre-commit hook configuration (prek)
└── uv.lock                 # Locked dependency manifest
```

---

## Disclaimer

This tool is intended for personal research only. Use it responsibly and in accordance with [AutoTrader's terms of service](https://www.autotrader.co.uk/content/legal/terms-of-use). Do not use it for commercial data collection or in ways that place excessive load on AutoTrader's servers.
