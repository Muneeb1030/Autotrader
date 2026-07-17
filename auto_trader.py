import json
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import pandas as pd
from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

enter_path_of_config_file = r"autotrader_config.csv"

# Used for parsing the specs text of each listing (not for search iteration)
_FUEL_TYPE_NAMES = [
    "Bi Fuel",
    "Diesel",
    "Diesel Hybrid",
    "Diesel Plug-in Hybrid",
    "Electric",
    "Petrol",
    "Petrol Hybrid",
    "Petrol Plug-in Hybrid",
    "Unlisted",
]

# Global Selenium instances — initialised in main()
driver: webdriver.Firefox  # type: ignore[assignment]
wait: WebDriverWait  # type: ignore[assignment]

# Extras price lookup — populated by _load_extras_prices() at startup
EXTRAS_PRICES: dict[str, int] = {}
EXTRA_COLUMNS: list[str] = []

# ---------------------------------------------------------------------------
# Logging — INFO+ to console, DEBUG+ to scraper.log
# ---------------------------------------------------------------------------
log = logging.getLogger("autotrader")
log.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)

_fh = logging.FileHandler("scraper.log", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log.addHandler(_ch)
log.addHandler(_fh)


# ---------------------------------------------------------------------------
# Extras helpers
# ---------------------------------------------------------------------------


def _load_extras_prices() -> None:
    """Populate EXTRAS_PRICES and EXTRA_COLUMNS from extras_prices.json."""
    global EXTRAS_PRICES, EXTRA_COLUMNS
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extras_prices.json")
    if not os.path.exists(path):
        log.warning("extras_prices.json not found at %s — extras feature disabled", path)
        return
    with open(path, encoding="utf-8") as f:
        EXTRAS_PRICES = json.load(f)
    EXTRA_COLUMNS = list(EXTRAS_PRICES.keys())
    log.info("Loaded %d extras from extras_prices.json", len(EXTRA_COLUMNS))


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_extra(extra_text: str) -> str | None:
    """Return the canonical name from EXTRA_COLUMNS that best matches *extra_text*.

    Pipeline:
    1. Exact match after normalisation.
    2. Keyword match — all words ≥4 chars from the canonical name must appear
       in the normalised extra text.
    """
    norm = _normalise(extra_text)
    for canonical in EXTRA_COLUMNS:
        if norm == _normalise(canonical):
            return canonical
    for canonical in EXTRA_COLUMNS:
        keywords = [w for w in _normalise(canonical).split() if len(w) >= 4]
        if keywords and all(kw in norm for kw in keywords):
            return canonical
    return None


# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------


def scroll_to_load_all(pause: float = 1.5, max_scrolls: int = 30) -> int:
    """Scroll until the page height stabilises, triggering infinite-scroll loads.

    Returns the number of scrolls performed.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    log.debug("scroll_to_load_all: initial page height %d", last_height)

    for scroll_n in range(1, max_scrolls + 1):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        listing_count = len(driver.find_elements(By.CSS_SELECTOR, 'a[data-testid="search-listing-title"]'))
        log.debug(
            "scroll_to_load_all: scroll %d — height %d → %d, listings visible: %d",
            scroll_n,
            last_height,
            new_height,
            listing_count,
        )
        if new_height == last_height:
            log.debug("scroll_to_load_all: stable after %d scroll(s)", scroll_n)
            return scroll_n
        last_height = new_height

    log.warning(
        "scroll_to_load_all: reached max_scrolls=%d — page may still have unloaded content",
        max_scrolls,
    )
    return max_scrolls


def setup_cookies() -> None:
    """Set AutoTrader consent cookies to bypass the Sourcepoint CMP dialog.

    ``atwv=1`` prevents the CMP script from loading; ``acceptATCookies=true``
    treats consent as already given.  Both must be set after navigating to the
    domain at least once.
    """
    log.info("Setting consent cookies on autotrader.co.uk")
    driver.get("https://www.autotrader.co.uk")
    time.sleep(2)
    for name, value in [("acceptATCookies", "true"), ("atwv", "1")]:
        try:
            driver.add_cookie({"name": name, "value": value, "domain": ".autotrader.co.uk", "path": "/"})
            log.debug("Cookie set: %s=%s", name, value)
        except Exception as e:
            log.warning("Failed to set cookie %s: %s", name, e)
    log.info("Consent cookies set — reloading")
    driver.refresh()
    time.sleep(1)


# ---------------------------------------------------------------------------
# Detail-page extras scraper
# ---------------------------------------------------------------------------


def get_extras(detail_url: str) -> tuple[dict[str, int], str]:
    """Navigate to *detail_url* and return matched extras with their list prices.

    Returns:
        matched   — ``{canonical_name: list_price_gbp}`` for every extra found
                    in the "Added extras" section that appears in the price table.
        unmatched — comma-separated string of extras present on the car but not
                    in the price table (captured in ``Extras_Other`` column).
    """
    if not EXTRA_COLUMNS:
        return {}, ""

    matched: dict[str, int] = {}
    unmatched_list: list[str] = []

    try:
        log.debug("get_extras: navigating to %s", detail_url)
        driver.get(detail_url)

        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1, [data-testid='advert-heading'], [data-testid='advert-title']"))
            )
        except Exception:
            log.warning("get_extras: page load timed out for %s", detail_url)
            return matched, ""

        time.sleep(1)

        # Log all data-testid values present — aids selector diagnosis
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        all_testids = sorted({el.get("data-testid") for el in page_soup.select("[data-testid]") if el.get("data-testid")})
        log.debug("get_extras: data-testid values on detail page: %s", all_testids)

        # --- Step 1: open "View all specs and features" panel ---
        # The confirmed data-testid from live pages is 'view-all-spec-and-features-signpost'
        view_specs_clicked = False

        for testid in [
            "view-all-spec-and-features-signpost",
            "view-all-specs",
            "specs-features-link",
            "all-specs-link",
            "view-specs",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, f'[data-testid="{testid}"]')
                driver.execute_script("arguments[0].click();", btn)
                view_specs_clicked = True
                log.debug("get_extras: clicked [data-testid='%s']", testid)
                time.sleep(2)
                break
            except Exception:
                pass

        if not view_specs_clicked:
            phrases = ["view all specs", "specs and features", "view specs", "all specs"]
            for phrase in phrases:
                try:
                    xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{phrase}")]'
                    btn = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].click();", btn)
                    view_specs_clicked = True
                    log.debug("get_extras: clicked element with text %r", phrase)
                    time.sleep(2)
                    break
                except Exception:
                    pass

        if not view_specs_clicked:
            log.debug("get_extras: 'View all specs' button not found on %s", detail_url)
            return matched, ""

        # Log updated data-testid values after opening the specs panel
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        post_click_testids = sorted({el.get("data-testid") for el in page_soup.select("[data-testid]") if el.get("data-testid")})
        log.debug("get_extras: data-testid values after specs click: %s", post_click_testids)

        # --- Step 2: expand "Added extras" accordion ---
        extras_expanded = False
        for phrase in ["added extras", "optional extras", "factory options"]:
            try:
                xpath = f'//*[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{phrase}")]'
                btn = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", btn)
                extras_expanded = True
                log.debug("get_extras: expanded accordion with text %r", phrase)
                time.sleep(2)
                break
            except Exception:
                pass

        if not extras_expanded:
            log.debug("get_extras: 'Added extras' accordion not found on %s", detail_url)
            # Don't return early — some pages list extras without an accordion heading

        # --- Step 3: parse the expanded extras list ---
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        extras_texts: list[str] = []

        # Strategy A: find heading containing "added extras", walk ancestors for li items
        for heading_node in page_soup.find_all(string=re.compile(r"added extras", re.IGNORECASE)):
            parent = heading_node.find_parent()
            if parent is None:
                continue
            for ancestor in [parent] + list(parent.parents)[:6]:
                items = ancestor.select("li") or ancestor.select("[data-testid*='extra']")
                candidate = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
                if len(candidate) >= 2:  # avoid picking up single-item nav elements
                    extras_texts = candidate
                    log.debug("get_extras: found %d extras via heading ancestor search", len(extras_texts))
                    break
            if extras_texts:
                break

        # Strategy B: data-testid selectors for extras container
        if not extras_texts:
            for sel in [
                '[data-testid="added-extras"]',
                '[data-testid*="extras-list"]',
                '[data-testid*="optional-extras"]',
            ]:
                container = page_soup.select_one(sel)
                if container:
                    items = container.select("li") or container.find_all(True, recursive=False)
                    extras_texts = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
                    log.debug("get_extras: found %d extras via selector %r", len(extras_texts), sel)
                    break

        log.debug("get_extras: raw extras texts: %s", extras_texts)

        for raw_text in extras_texts:
            # AutoTrader appends "Added extra" (badge text) directly inside the same element.
            # Strip it so matching works on the actual extra name.
            text = re.sub(r"\s*Added\s+extra\s*$", "", raw_text, flags=re.IGNORECASE).strip()
            if not text:
                continue
            canonical = _match_extra(text)
            if canonical:
                matched[canonical] = EXTRAS_PRICES[canonical]
                log.debug("get_extras: matched %r → %r (£%d)", text, canonical, EXTRAS_PRICES[canonical])
            else:
                unmatched_list.append(text)
                log.debug("get_extras: unmatched extra: %r", text)

        log.info(
            "get_extras: %d matched, %d unmatched — %s",
            len(matched),
            len(unmatched_list),
            detail_url,
        )

    except Exception as e:
        log.warning("get_extras: unexpected error for %s: %s", detail_url, e)

    return matched, ", ".join(unmatched_list)


def _title_matches(title: str, make: str, model: str) -> bool:
    """Return True if *title* contains both the make and model name (case-insensitive)."""
    lower = title.lower()
    if make and make.lower() not in lower:
        return False
    if model and model.lower() not in lower:
        return False
    return True


# ---------------------------------------------------------------------------
# Search-page scraping
# ---------------------------------------------------------------------------


def get_total_pages(
    postcode: str,
    make: str,
    model: str,
    trim: str,
    fuel: str,
    year_from: str,
    year_to: str,
    radius: str,
    price_to: str,
    output_file: str,
    page_start: int = 1,
) -> None:
    try:
        base_url = "https://www.autotrader.co.uk/car-search"
        encoded_postcode = quote_plus(postcode)
        encoded_make = quote_plus(make)
        encoded_model = quote_plus(model) if model else ""
        encoded_fuel = quote_plus(fuel)
        encoded_trim = quote_plus(trim) if trim else ""

        model_param = f"&model={encoded_model}" if encoded_model else ""
        trim_param = f"&aggregatedTrim={encoded_trim}" if encoded_trim else ""
        price_to_param = f"&price-to={quote_plus(price_to)}" if price_to else ""

        count_url = (
            f"{base_url}?fuel-type={encoded_fuel}&make={encoded_make}{model_param}{trim_param}"
            f"&postcode={encoded_postcode}&radius={radius}{price_to_param}"
            f"&year-from={year_from}&year-to={year_to}"
        )
        log.debug("Loading search URL: %s", count_url)
        driver.get(count_url)
        time.sleep(3)
        count_html = driver.page_source
        count_soup = BeautifulSoup(count_html, "html.parser")
        pagination_element = count_soup.find("p", {"data-testid": "pagination-show"})
        if pagination_element:
            pagination_text = pagination_element.text.strip()
            log.debug("Pagination text: %r", pagination_text)
            m = re.search(r"Page \d+ of (\d+)", pagination_text)
            if m:
                total_pages = min(101, int(m.group(1)))
            else:
                log.warning("Pagination element found but pattern did not match. Raw: %r", pagination_text)
                total_pages = 1
        else:
            log.warning(
                "Pagination element [data-testid='pagination-show'] not found — assuming 1 page. "
                "Check if the page loaded correctly or if the selector has changed. "
                "Current URL: %s",
                driver.current_url,
            )
            log.debug("Page HTML snippet (first 2000 chars):\n%s", count_html[:2000])
            total_pages = 1

        log.info(
            "Total pages for make=%r model=%r trim=%r fuel=%r: %d",
            make,
            model,
            trim,
            fuel,
            total_pages,
        )

        for page_number in range(page_start, total_pages + 1):
            try:
                cars_data: list[dict] = []
                page_url = (
                    f"{base_url}?page={page_number}&fuel-type={encoded_fuel}&make={encoded_make}"
                    f"{model_param}{trim_param}&postcode={postcode}&radius={radius}"
                    f"{price_to_param}&year-from={year_from}&year-to={year_to}"
                )
                log.info("Fetching page %d / %d  — %s", page_number, total_pages, page_url)
                driver.get(page_url)

                try:
                    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[data-testid="search-listing-title"]')))
                    log.debug("Listing cards rendered on page %d", page_number)
                except Exception:
                    log.warning(
                        "Timed out (60 s) waiting for listing cards on page %d. Current URL: %s — skipping page.",
                        page_number,
                        driver.current_url,
                    )
                    continue

                # Brief pause so React finishes hydrating the infinite-scroll container
                time.sleep(2)
                scrolls = scroll_to_load_all()
                log.info("Page %d: scrolling complete (%d scrolls)", page_number, scrolls)

                page_html = driver.page_source
                soup = BeautifulSoup(page_html, "html.parser")

                # Collect unique parent <li> elements for each listing anchor
                title_links = soup.select('a[data-testid="search-listing-title"]')
                seen: set[int] = set()
                li_elements = []
                for link in title_links:
                    parent_li = link.find_parent("li")
                    if parent_li and id(parent_li) not in seen:
                        seen.add(id(parent_li))
                        li_elements.append(parent_li)

                log.debug(
                    "Page %d: found %d listing cards via search-listing-title anchors",
                    page_number,
                    len(li_elements),
                )
                if not li_elements:
                    log.warning(
                        "Page %d: 0 listing cards found after full scroll. Page title: %r  Current URL: %s",
                        page_number,
                        soup.title.text if soup.title else "N/A",
                        driver.current_url,
                    )
                    log.debug("Page %d HTML snippet (first 3000 chars):\n%s", page_number, page_html[:3000])

                # --- Pass 1: extract all search-page data (no navigation) ---
                raw_listings: list[dict] = []
                for idx, li_element in enumerate(li_elements):
                    title = get_title(li_element)
                    price_text = get_price(li_element)
                    year, miles, fuel_type = get_other_car_data(li_element, default_fuel=fuel)

                    if title is None:
                        log.warning(
                            "Page %d listing #%d: title not found. Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if price_text is None:
                        log.warning(
                            "Page %d listing #%d: price not found. Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if miles == "NA":
                        log.debug("Page %d listing #%d: miles not found", page_number, idx)

                    log.debug(
                        "Page %d listing #%d: title=%r price=%r year=%r miles=%r fuel=%r",
                        page_number,
                        idx,
                        title,
                        price_text,
                        year,
                        miles,
                        fuel_type,
                    )

                    # Build clean detail URL (strip search query params)
                    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
                    href = anchor.get("href") if anchor else None
                    if href and isinstance(href, str) and href.startswith("/"):
                        detail_url: str | None = "https://www.autotrader.co.uk" + href.split("?")[0]
                    else:
                        detail_url = None

                    raw_listings.append(
                        {
                            "Title": title,
                            "Price": price_text,
                            "Year": year,
                            "Miles": miles,
                            "Fuel_Type": fuel_type,
                            "_detail_url": detail_url,
                        }
                    )

                # --- Filter: drop listings that don't match the expected make/model ---
                # AutoTrader occasionally returns unrelated makes in search results.
                # Filter before visiting detail pages to avoid wasting time.
                before_filter = len(raw_listings)
                raw_listings = [r for r in raw_listings if _title_matches(r.get("Title") or "", make, model)]
                skipped = before_filter - len(raw_listings)
                if skipped:
                    log.info(
                        "Page %d: filtered out %d listing(s) not matching make=%r model=%r",
                        page_number,
                        skipped,
                        make,
                        model,
                    )

                # --- Pass 2: visit each detail page and collect extras ---
                for raw in raw_listings:
                    d_url: str | None = raw.pop("_detail_url")
                    raw["Detail_URL"] = d_url or ""
                    if d_url and EXTRA_COLUMNS:
                        extras_matched, extras_other = get_extras(d_url)
                        raw.update(extras_matched)
                        raw["Extras_Other"] = extras_other
                        raw["Extras_Total"] = sum(extras_matched.values())
                    else:
                        raw["Extras_Other"] = ""
                        raw["Extras_Total"] = 0
                    cars_data.append(raw)

                log.info("Page %d: extracted %d raw listings, saving...", page_number, len(cars_data))
                save_data(output_file, cars_data)

            except Exception as e:
                log.exception("Error processing page %d (%s): %s", page_number, page_url, e)
                with open("error_log.txt", "a") as log_f:
                    log_f.write(f"An error occurred: {e}\n")
                    log_f.write(f"{page_url}\n")

    except Exception as e:
        log.exception("Fatal error in get_total_pages (make=%r model=%r fuel=%r): %s", make, model, fuel, e)


# ---------------------------------------------------------------------------
# Data extractors — search-page listing cards
# ---------------------------------------------------------------------------


def get_make_data() -> list[tuple[str, str, str, str, str]]:
    """Return ``[(uriValue, model, fuelType, aggregatedTrim, price_to)]`` from make.json.

    The list is reversed so that the last entry in the file is processed first
    (legacy behaviour — preserves run order from earlier versions).
    """
    with open("make.json", encoding="utf-8") as f:
        json_list = json.load(f)
    entries: list[tuple[str, str, str, str, str]] = [
        (
            entry["uriValue"],
            entry.get("model", ""),
            entry.get("fuelType", ""),
            entry.get("aggregatedTrim", ""),
            str(entry["price-to"]) if entry.get("price-to") else "",
        )
        for entry in json_list
    ][::-1]
    log.debug("get_make_data: loaded %d make/model entries", len(entries))
    return entries


def get_other_car_data(li_element: Tag, default_fuel: str = "NA") -> tuple[str, str, str]:
    """Extract year, mileage, and fuel type from a search-result listing card.

    Tries a structured specs container first; falls back to full-card text scan
    when AutoTrader changes its data-testid attributes.
    """
    year = miles = fuel_type = "NA"

    testids = [el.get("data-testid") for el in li_element.select("[data-testid]")]
    log.debug("get_other_car_data: data-testid attrs in card: %s", testids)

    specs_container = (
        li_element.select_one('[data-testid="search-listing-specs"]')
        or li_element.select_one('[data-testid*="spec"]')
        or li_element.select_one('[data-testid*="key-spec"]')
    )

    if specs_container:
        spec_items = specs_container.select("li") or specs_container.find_all(True, recursive=False)
        if spec_items:
            li_text_combined = " ".join(item.get_text(strip=True) for item in spec_items)
            log.debug("get_other_car_data: specs container text: %r", li_text_combined)
            year = spec_items[0].get_text(strip=True)
            miles_match = re.search(r"(\d[\d,]*\s*miles?)", li_text_combined, re.IGNORECASE)
            fuel_match = re.search(
                r"\b(" + "|".join(map(re.escape, _FUEL_TYPE_NAMES)) + r")\b",
                li_text_combined,
                re.IGNORECASE,
            )
            if miles_match:
                miles = miles_match.group(1)
            if fuel_match:
                fuel_type = fuel_match.group(1)
            if fuel_type == "NA" and default_fuel != "NA":
                fuel_type = default_fuel
            return year, miles, fuel_type

    # Fallback: regex scan of all text in the card
    full_text = li_element.get_text(separator=" ", strip=True)
    log.debug("get_other_car_data: full card text (first 400 chars): %r", full_text[:400])

    year_match = re.search(r"\b(20[1-3]\d)\b", full_text)
    if year_match:
        year = year_match.group(1)

    miles_match = re.search(r"(\d[\d,]*\s*miles?)", full_text, re.IGNORECASE)
    if miles_match:
        miles = miles_match.group(1)

    fuel_match = re.search(
        r"\b(" + "|".join(map(re.escape, _FUEL_TYPE_NAMES)) + r")\b",
        full_text,
        re.IGNORECASE,
    )
    if fuel_match:
        fuel_type = fuel_match.group(1)

    if fuel_type == "NA" and default_fuel != "NA":
        fuel_type = default_fuel

    if year == "NA" or miles == "NA":
        log.debug(
            "get_other_car_data: fallback result — year=%r miles=%r fuel=%r. Li HTML (first 1500 chars):\n%s",
            year,
            miles,
            fuel_type,
            li_element.prettify()[:1500],
        )

    return year, miles, fuel_type


def get_title(li_element: Tag) -> str | None:
    """Return the listing title from the search-listing-title anchor text."""
    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
    if not anchor:
        log.debug("get_title: anchor a[data-testid='search-listing-title'] not found")
        return None
    text = anchor.get_text(strip=True)
    if not text:
        log.debug("get_title: anchor found but empty. Anchor HTML: %s", str(anchor)[:300])
        return None
    # AutoTrader appends the price to the anchor text (e.g. ", £39,995") — strip it.
    text = re.sub(r",\s*£[\d,]+\s*$", "", text).strip()
    return text if text else None


def get_price(li_element: Tag) -> str | None:
    """Return the listing price (digits only) from a search-result card."""
    # Try a specific price element — extract only the £ figure, not surrounding spec text
    price_el = li_element.select_one('[data-testid*="price"]')
    if price_el:
        m = re.search(r"£([\d,]+)", price_el.get_text(strip=True))
        if m:
            return m.group(1).replace(",", "")

    # Fallback: price is appended to the title anchor text as ", £XX,XXX"
    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
    if anchor:
        m = re.search(r"£([\d,]+)\s*$", anchor.get_text(strip=True))
        if m:
            return m.group(1).replace(",", "")

    # Last resort: any text node matching £\d
    for el in li_element.find_all(string=re.compile(r"£\s*\d")):
        m = re.search(r"£([\d,]+)", str(el))
        if m:
            return m.group(1).replace(",", "")

    log.debug(
        "get_price: no price found. Candidate spans: %s",
        [str(s)[:80] for s in li_element.find_all("span") if "£" in s.get_text()],
    )
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def get_file_name() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"autotrader_info_{timestamp}.xlsx"


def save_data(file: str, cars_data: list[dict]) -> int:
    """Append *cars_data* to the xlsx file, deduplicating on core columns.

    Returns the number of rows written.
    """
    if not cars_data:
        log.debug("save_data: no data to save")
        return 0

    core_cols = ["Title", "Price", "Extras_Total", "Year", "Miles", "Fuel_Type", "Detail_URL"]
    tail_cols = ["Extras_Other"]
    all_cols = core_cols + EXTRA_COLUMNS + tail_cols

    try:
        older = pd.read_excel(file)
    except FileNotFoundError:
        older = pd.DataFrame(columns=all_cols)

    df = pd.DataFrame(cars_data)

    # Ensure all extra columns are present; fill missing with 0
    for col in EXTRA_COLUMNS:
        if col not in df.columns:
            df[col] = 0
        if col not in older.columns:
            older[col] = 0

    for col in tail_cols:
        if col not in df.columns:
            df[col] = ""
        if col not in older.columns:
            older[col] = ""

    # Ensure Extras_Total and Detail_URL columns are present
    for col, default in [("Extras_Total", 0), ("Detail_URL", "")]:
        if col not in df.columns:
            df[col] = default
        if col not in older.columns:
            older[col] = default

    # Recompute Extras_Total from individual extra columns
    if EXTRA_COLUMNS:
        df["Extras_Total"] = df[EXTRA_COLUMNS].sum(axis=1).astype(int)

    combined_df = pd.concat([older, df], ignore_index=True)
    df_dedup = combined_df.drop_duplicates(subset=["Title", "Price", "Year", "Miles", "Fuel_Type"])
    df_clean = df_dedup.dropna(subset=["Title", "Price"])

    # Reorder to canonical column sequence
    available = [c for c in all_cols if c in df_clean.columns]
    extra_found = [c for c in df_clean.columns if c not in all_cols]
    df_clean = df_clean[available + extra_found]

    row_count = len(df_clean)
    log.info("save_data: writing %d rows to %s", row_count, file)
    df_clean.to_excel(file, index=False)
    return row_count


def get_config(filename: str) -> None:
    log.info("Loading config from: %s", filename)
    df = pd.read_csv(filename)
    log.info("Config rows: %d", len(df))
    output_file = get_file_name()
    log.info("Output file (if data found): %s", output_file)
    makes = get_make_data()
    log.info("Makes loaded: %d", len(makes))

    for row_num, (_index, row) in enumerate(df.iterrows(), start=1):
        postal_code = row["PostalCode"]
        page_number = row["PageNumber"]
        year_from = row["year-from"]
        year_to = row["year-to"]
        radius = row["radius"]
        for make, model, fuel_type, trim, price_to in makes:
            log.info(
                "Row %d — postcode=%r make=%r model=%r trim=%r fuel=%r years=%s–%s radius=%s price-to=%s start_page=%s",
                row_num,
                postal_code,
                make,
                model or "(all)",
                trim or "(all)",
                fuel_type,
                year_from,
                year_to,
                radius,
                price_to or "(any)",
                page_number,
            )
            time.sleep(1)
            get_total_pages(
                postal_code,
                make,
                model,
                trim,
                fuel_type,
                str(year_from),
                str(year_to),
                str(radius),
                price_to,
                output_file,
                int(page_number),
            )

    if os.path.exists(output_file):
        saved_df = pd.read_excel(output_file)
        log.info("Scrape complete. %d car(s) saved to %s", len(saved_df), output_file)
    else:
        log.info("Scrape complete. 0 cars scraped — no output file created.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    global driver, wait
    log.info("Starting AutoTrader scraper")
    _load_extras_prices()
    options = Options()
    # 'eager' fires after DOMContentLoaded — avoids hanging on ad/tracking resources
    options.page_load_strategy = "eager"
    driver = webdriver.Firefox(options=options)
    driver.maximize_window()
    wait = WebDriverWait(driver, 360)
    try:
        setup_cookies()
        get_config(enter_path_of_config_file)
    finally:
        log.info("Quitting browser")
        driver.quit()
    log.info("DONE")


if __name__ == "__main__":
    main()
