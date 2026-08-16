# Implementation Plan: Scraping "Added Extras" from Individual Car Listings

## Problem Statement

The current scraper collects summary data from the search results page (title, price, year, miles, fuel type). It does **not** visit individual listing pages. AutoTrader hides the full options list behind a "View all specs and features" panel on each car's detail page, inside an "Added extras" accordion.

This plan adds a second-pass step: after collecting a listing card from the search page, navigate to the car's detail URL, extract all "Added extras", match them against a known price table, write each extra as a dedicated column in the spreadsheet, and sum the extras into an `Extras_Total` column.

---

## Data Flow (New)

```
Search results page
  └─ listing card (li element)
       ├─ [existing] title, price, year, miles, fuel_type
       └─ [new] detail_url  ─────────────────────────────►  Car detail page
                                                                  └─ Click "View all specs and features"
                                                                       └─ Expand "Added extras" accordion
                                                                            └─ Extract extras text list
                                                                                 └─ Match → extras_prices.json
                                                                                      └─ {extra_col: price, ...}
                                                                                           └─ Extras_Total = Σ prices
```

---

## Extras Price Table

Stored in `extras_prices.json` at repo root. Each key is the **canonical display name** used for the spreadsheet column header and fuzzy-matching anchor. Value is the integer GBP list price.

| Canonical Name | Price |
|---|---|
| Offroad Design Package | 1334 |
| Offroad Design Package with Inlays in High Gloss Black | 1602 |
| Metallic Paint | 774 |
| Special Paint | 1683 |
| Panoramic Roof Fixed Glass | 1137 |
| Roof Rails in Aluminium | 413 |
| Roof Rails in Black High Gloss | 413 |
| Side Window Trims in Black High Gloss | 248 |
| Electric Folding Exterior Mirrors | 210 |
| 20-inch Taycan Turbo S Aero Design Wheels | 1776 |
| 21-inch Cross Turismo Design Wheels | 3245 |
| 21-inch Offroad Design Wheels | 3245 |
| Rear-Axle Steering including Power Steering Plus | 1650 |
| Porsche Ceramic Composite Brake | 6321 |
| Porsche Surface Coated Brake with Calipers in White | 2105 |
| Leather Interior Smooth-Finish | 2538 |
| Two-Tone Leather Interior | 2874 |
| Comfort Seats in Front 14-Way Electric with Memory Package | 1170 |
| Adaptive Sports Seats in Front 18-Way Electric with Memory Package | 1446 |
| 4+1 Seating System Rear 2+1 Bench | 336 |
| Seat Heating Front and Rear | 308 |
| Seat Ventilation Front | 716 |
| Heated GT Sports Steering Wheel in Leather | 194 |
| Passenger Display | 725 |
| BOSE Surround Sound System | 956 |
| Burmester High-End 3D Surround Sound System | 4200 |
| Head-Up Display | 1128 |
| Advanced 4-Zone Climate Control | 581 |
| ParkAssist including Surround View | 1022 |
| Adaptive Cruise Control | 1238 |
| InnoDrive including Adaptive Cruise Control | 2172 |
| LED Matrix Main Headlights inc PDLS Plus | 1221 |
| Comfort Access | 774 |
| Lane Change Assist | 548 |
| 150kW DC On-Board Booster | 294 |
| 22kW AC On-Board Charger | 1179 |
| Public Charging Cable Mode 3 | 210 |
| Mobile Charger Connect | 767 |

---

## Spreadsheet Column Layout

**Existing columns** (unchanged):
`Title | Price | Year | Miles | Fuel_Type`

**New columns added** (one per canonical extra, in order from the table above, plus a total):
`Offroad Design Package | Offroad Design Package with Inlays in High Gloss Black | ... | Mobile Charger Connect | Extras_Total`

- Cell value = **integer GBP list price** when the extra is present on the car (e.g. `1137`)
- Cell value = **0** when absent
- `Extras_Total` = row sum of all extra columns

---

## Extra Matching Strategy

AutoTrader displays extras as free-text labels (e.g. `"Panoramic roof system (fixed glass)"`). These won't exactly match canonical names. Matching pipeline per extracted extra string:

1. **Normalise**: lowercase, remove punctuation, collapse whitespace
2. **Exact match**: normalised extra == normalised canonical name → match
3. **Keyword match**: all significant words from canonical name appear in the normalised extra string → match
4. **No match**: extra logged at DEBUG level under `Extras_Other` column as a comma-separated string (captures extras not in the price list)

This approach requires no extra dependencies.

---

## Detail Page Navigation

- **URL**: extracted from `href` attribute of `a[data-testid="search-listing-title"]` on the search page. Prefix with `https://www.autotrader.co.uk` if relative.
- **Click target 1**: element whose text contains `"View all specs"` (or `data-testid="view-all-specs"` if present)
- **Click target 2**: element whose text is `"Added extras"` within the opened panel
- **Wait**: `WebDriverWait(driver, 60)` for the detail page heading; 10 s for the specs button
- **Back navigation**: `driver.back()` after extraction (faster than re-navigating to the search page)
- **Timeout handling**: if the detail page or either button is not found within the timeout, log a warning and record all extras as 0 for that listing

---

## Files to Create / Modify

| File | Change |
|---|---|
| `specs/extras.md` | ✅ This file — plan |
| `extras_prices.json` | **Create** — canonical name → integer price dict |
| `auto_trader.py` | **Modify** — add `get_extras()`, update `get_total_pages()`, `save_data()`, `get_config()` |

### `auto_trader.py` changes in detail

**New function `get_extras(detail_url: str) -> dict[str, int]`**
Navigates to the detail page, clicks the two accordion controls, parses the extras list, returns `{canonical_name: price}` for every matched extra. Logs unmatched extras at DEBUG.

**Update `get_total_pages()`**
After `get_title()` / `get_price()` / `get_other_car_data()`, extract `href` from the title anchor and call `get_extras(full_url)`. Merge the returned dict into the `cars_data` row. Navigate back with `driver.back()` and re-wait for the search listing anchor before continuing.

**Update `save_data()`**
- Initialise the DataFrame with the full column list (existing 5 + all 38 extra columns + `Extras_Total` + `Extras_Other`)
- Fill missing extra columns with `0` before writing
- Compute `Extras_Total` as row sum of extra columns

**Update `get_config()` / module-level**
Load `extras_prices.json` once at startup into a module-level constant `EXTRAS_PRICES: dict[str, int]` and `EXTRA_COLUMNS: list[str]` (ordered list of canonical names). Pass to or import into `get_extras()`.

---

## Documentation Updates

`.github/copilot-instructions.md` must be updated to reflect:
- New `extras_prices.json` file and its role
- New `EXTRAS_PRICES` / `EXTRA_COLUMNS` module-level constants
- New `get_extras()` function and detail-page navigation pattern
- Updated spreadsheet column layout (5 core + 38 extra + Extras_Total + Extras_Other)
- Updated data flow description to include the second-pass detail page visit

---

## Todos (for SQL tracking)

1. `create-extras-json` — Create `extras_prices.json` with all 38 entries
2. `load-extras-at-startup` — Load `extras_prices.json` into module-level constants
3. `add-get-extras-fn` — Implement `get_extras(detail_url)` in `auto_trader.py`
4. `update-get-total-pages` — Integrate `get_extras()` call per listing; extract href; navigate back
5. `update-save-data` — Add extra columns + `Extras_Total` + `Extras_Other` to DataFrame logic
6. `update-docs` — Update `.github/copilot-instructions.md`
7. `validate-run` — Execute `just run` and confirm xlsx > target size with extra columns populated; then run `just check` to confirm lint/type-check pass
