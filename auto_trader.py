import json
import logging
import os
import re
import time
from datetime import datetime
from time import sleep
from urllib.parse import quote_plus

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

enter_path_of_config_file = r"autotrader_config.csv"

# Used only for parsing the specs text of each listing, not for search iteration
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

# ---------------------------------------------------------------------------
# Logging setup — INFO+ to console, DEBUG+ to scraper.log
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


def scroll_to_load_all(pause: float = 1.5, max_scrolls: int = 30) -> int:
    """Scroll the page incrementally until no new content loads.

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


def setup_cookies():
    """Set AutoTrader consent cookies to bypass the Sourcepoint CMP dialog.

    The site checks for ``atwv=1`` to skip loading the CMP script entirely, and
    ``acceptATCookies=true`` to treat consent as already given. Without these,
    the consent wall blocks listings from rendering.

    Must be called after the browser has made at least one request to the
    autotrader.co.uk domain so the cookies can be scoped correctly.
    """
    log.info("Setting consent cookies on autotrader.co.uk")
    driver.get("https://www.autotrader.co.uk")
    time.sleep(2)
    for name, value in [("acceptATCookies", "true"), ("atwv", "1")]:
        try:
            driver.add_cookie(
                {"name": name, "value": value, "domain": ".autotrader.co.uk", "path": "/"}
            )
            log.debug("Cookie set: %s=%s", name, value)
        except Exception as e:
            log.warning("Failed to set cookie %s: %s", name, e)
    log.info("Consent cookies set — reloading")
    driver.refresh()
    time.sleep(1)


def get_total_pages(postcode, make, model, trim, fuel, year_from, year_to, radius, output_file, page_start=1):
    try:
        url = "https://www.autotrader.co.uk/car-search"
        encoded_postcode = quote_plus(postcode)
        encoded_make = quote_plus(make)
        encoded_model = quote_plus(model) if model else ""
        encoded_fuel = quote_plus(fuel)

        model_param = f"&model={encoded_model}" if encoded_model else ""
        encoded_trim = quote_plus(trim) if trim else ""
        trim_param = f"&aggregatedTrim={encoded_trim}" if encoded_trim else ""
        page_url = (
            f"{url}?fuel-type={encoded_fuel}&make={encoded_make}{model_param}{trim_param}"
            f"&postcode={encoded_postcode}&radius={radius}&year-from={year_from}&year-to={year_to}"
        )
        log.debug("Loading search URL: %s", page_url)
        driver.get(page_url)
        time.sleep(3)
        page_html = driver.page_source
        soup = BeautifulSoup(page_html, "html.parser")
        pagination_element = soup.find("p", {"data-testid": "pagination-show"})
        if pagination_element:
            pagination_text = pagination_element.text.strip()
            log.debug("Pagination text: %r", pagination_text)
            match = re.search(r"Page \d+ of (\d+)", pagination_text)
            total_pages = 101
            if match:
                total_pages = int(match.group(1))
                total_pages = min(101, total_pages)
            else:
                log.warning(
                    "Pagination element found but pattern didn't match. Raw text: %r",
                    pagination_text,
                )
        else:
            log.warning(
                "Pagination element [data-testid='pagination-show'] not found — "
                "assuming 1 page. Check if the page loaded correctly or if the "
                "selector has changed. Current URL: %s",
                driver.current_url,
            )
            body_snippet = page_html[:2000] if page_html else "<empty>"
            log.debug("Page HTML snippet (first 2000 chars):\n%s", body_snippet)
            total_pages = 1

        log.info("Total pages for make=%r model=%r trim=%r fuel=%r: %d", make, model, trim, fuel, total_pages)

        for page_number in range(page_start, total_pages + 1):
            try:
                cars_data = []
                page_url = (
                    f"{url}?page={page_number}&fuel-type={fuel}&make={make}{model_param}{trim_param}"
                    f"&postcode={postcode}&radius={radius}&year-from={year_from}&year-to={year_to}"
                )
                log.info("Fetching page %d / %d  — %s", page_number, total_pages, page_url)
                driver.get(page_url)

                # Wait up to 60 s for actual listing cards to appear (not skeletons).
                # `a[data-testid="search-listing-title"]` is only present once React
                # has replaced the skeleton placeholders with real data.
                try:
                    WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, 'a[data-testid="search-listing-title"]')
                        )
                    )
                    log.debug("Listing cards rendered on page %d", page_number)
                except Exception:
                    log.warning(
                        "Timed out (60 s) waiting for listing cards on page %d. "
                        "Current URL: %s — skipping page.",
                        page_number,
                        driver.current_url,
                    )
                    continue

                # Brief pause to let React finish hydrating the infinite-scroll
                # container before we start measuring page height.
                time.sleep(2)

                # Scroll to trigger lazy-loading of all listings
                scrolls = scroll_to_load_all()
                log.info("Page %d: scrolling complete (%d scrolls)", page_number, scrolls)

                page_html = driver.page_source
                soup = BeautifulSoup(page_html, "html.parser")

                # Find all real listing cards via their title anchor.
                # Selector-drift-resistant: only matches loaded cards, not skeleton
                # placeholders, ads, or container elements.
                title_links = soup.select('a[data-testid="search-listing-title"]')
                seen: set = set()
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
                        "Page %d: 0 listing cards found after full scroll. "
                        "Page title: %r  Current URL: %s",
                        page_number,
                        soup.title.text if soup.title else "N/A",
                        driver.current_url,
                    )
                    log.debug(
                        "Page %d HTML snippet (first 3000 chars):\n%s",
                        page_number,
                        page_html[:3000],
                    )

                for idx, li_element in enumerate(li_elements):
                    title = get_title(li_element)
                    price_text = get_price(li_element)
                    year, miles, fuel_type = get_other_car_data(li_element, default_fuel=fuel)

                    if title is None:
                        log.warning(
                            "Page %d listing #%d: title not found. Selector: a[data-testid='search-listing-title'] > h3. Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if price_text is None:
                        log.warning(
                            "Page %d listing #%d: price not found. "
                            "Selector: span.at__sc-1mc7cl3-5.edXwbj (auto-generated class — "
                            "likely stale if AutoTrader redeployed). "
                            "Li HTML: %s",
                            page_number,
                            idx,
                            li_element.prettify()[:500],
                        )
                    if miles == "NA" or fuel_type == "NA":
                        log.debug(
                            "Page %d listing #%d: miles=%r fuel_type=%r — specs li text: %r",
                            page_number,
                            idx,
                            miles,
                            fuel_type,
                            " | ".join(li.text for ul in li_element.select('ul[data-testid="search-listing-specs"]') for li in ul.select("li")),
                        )

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
                    cars_data.append(
                        {
                            "Title": title,
                            "Price": price_text,
                            "Year": year,
                            "Miles": miles,
                            "Fuel_Type": fuel_type,
                        }
                    )

                log.info("Page %d: extracted %d raw listings, saving...", page_number, len(cars_data))
                save_data(output_file, cars_data)

            except Exception as e:
                log.exception("Error processing page %d (%s): %s", page_number, page_url, e)
                with open("error_log.txt", "a") as log_f:
                    log_f.write(f"An error occurred: {e}\n")
                    log_f.write(f"{page_url}\n")
    except Exception as e:
        log.exception("Fatal error in get_total_pages (make=%r model=%r trim=%r fuel=%r): %s", make, model, trim, fuel, e)


def get_make_data():
    """Return list of (uriValue, model, fuelType, aggregatedTrim) tuples from make.json.

    ``model``, ``fuelType``, and ``aggregatedTrim`` default to empty string if not set.
    """
    with open("make.json", "r") as f:
        json_list = json.load(f)

    entries = [
        (
            entry["uriValue"],
            entry.get("model", ""),
            entry.get("fuelType", ""),
            entry.get("aggregatedTrim", ""),
        )
        for entry in json_list
    ][::-1]
    log.debug("get_make_data: loaded %d make/model entries", len(entries))
    return entries


def get_other_car_data(li_element, default_fuel: str = "NA"):
    year = miles = fuel_type = "NA"

    # Log all data-testid attributes present in this card for selector diagnosis
    testids = [el.get("data-testid") for el in li_element.find_all(attrs={"data-testid": True})]
    log.debug("get_other_car_data: data-testid attrs in card: %s", testids)

    # --- 1. Try known specs container selectors (broadest to narrowest) ---
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
            return year, miles, fuel_type

    # --- 2. Fallback: scan the full card text for year, mileage, fuel type ---
    # AutoTrader may change data-testid values; text patterns are stable.
    full_text = li_element.get_text(separator=" ", strip=True)
    log.debug("get_other_car_data: full card text (first 400 chars): %r", full_text[:400])

    # Year: 4-digit year in the range 2010–2035, optionally followed by plate info e.g. "(21 reg)"
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
            "get_other_car_data: fallback scan result — year=%r miles=%r fuel=%r. "
            "Li HTML (first 1500 chars):\n%s",
            year,
            miles,
            fuel_type,
            li_element.prettify()[:1500],
        )

    return year, miles, fuel_type


def get_title(li_element):
    anchor = li_element.select_one('a[data-testid="search-listing-title"]')
    if not anchor:
        log.debug("get_title: anchor a[data-testid='search-listing-title'] not found")
        return None
    # Title text sits directly inside the anchor (no h3 wrapper in current markup)
    title = anchor.get_text(strip=True)
    if not title:
        log.debug("get_title: anchor found but contained no text. HTML: %s", anchor.prettify()[:400])
        return None
    return title


def get_price(li_element):
    # Try stable data-testid selector first
    price_elem = li_element.select_one('[data-testid*="price"]')
    if price_elem:
        return price_elem.get_text(strip=True).replace("£", "").replace(",", "").strip()

    # Fall back: any element whose text starts with £
    for elem in li_element.find_all(string=re.compile(r"^£[\d,]+")):
        return str(elem).replace("£", "").replace(",", "").strip()

    log.debug(
        "get_price: no price element found. Li HTML snippet: %s",
        li_element.prettify()[:600],
    )
    return None


def get_config(filename):
    log.info("Loading config from: %s", filename)
    df = pd.read_csv(filename)
    log.info("Config rows: %d", len(df))
    output_file = get_file_name()
    log.info("Output file (if data found): %s", output_file)
    makes = get_make_data()
    log.info("Makes loaded: %d", len(makes))
    for index, row in df.iterrows():
        postal_code = row["PostalCode"]
        page_number = row["PageNumber"]
        year_from = row["year-from"]
        year_to = row["year-to"]
        radius = row["radius"]
        for make, model, fuel_type, trim in makes:
            log.info(
                "Row %d — postcode=%r make=%r model=%r trim=%r fuel=%r years=%s–%s radius=%s start_page=%s",
                index + 1,
                postal_code,
                make,
                model or "(all)",
                trim or "(all)",
                fuel_type,
                year_from,
                year_to,
                radius,
                page_number,
            )
            sleep(1)
            get_total_pages(postal_code, make, model, trim, fuel_type, year_from, year_to, radius, output_file, page_number)

    if os.path.exists(output_file):
        saved_df = pd.read_excel(output_file)
        total_rows = len(saved_df)
        log.info("Scrape complete. %d car(s) saved to %s", total_rows, output_file)
    else:
        log.warning(
            "Scrape complete. 0 cars scraped — no output file was created. "
            "Check the WARNING/DEBUG lines above for selector or consent issues."
        )


def save_data(output_file, cars_data) -> int:
    """Save cars_data to the xlsx file, deduplicating against existing rows.

    Returns the number of rows written to the file after this call.
    """
    if not cars_data:
        log.warning("save_data called with empty cars_data — nothing to save")
        return 0

    log.debug("save_data: %d rows incoming", len(cars_data))
    try:
        older = pd.read_excel(output_file)
        log.debug("save_data: existing file has %d rows", len(older))
    except FileNotFoundError:
        log.debug("save_data: output file not yet created, starting fresh")
        older = pd.DataFrame(columns=["Title", "Price", "Year", "Miles", "Fuel_Type"])

    df = pd.DataFrame(cars_data)
    combined_df = pd.concat([older, df], ignore_index=True)
    log.debug("save_data: combined rows (before dedup): %d", len(combined_df))

    df_no_duplicates = combined_df.drop_duplicates(subset=["Title", "Price", "Year", "Miles", "Fuel_Type"])
    dupes_removed = len(combined_df) - len(df_no_duplicates)
    if dupes_removed:
        log.debug("save_data: removed %d duplicate rows", dupes_removed)

    rows_before_dropna = len(df_no_duplicates)
    df_no_na = df_no_duplicates.dropna(subset=["Title", "Price"])
    na_removed = rows_before_dropna - len(df_no_na)
    if na_removed:
        dropped = df_no_duplicates[df_no_duplicates[["Title", "Price"]].isna().any(axis=1)]
        log.warning(
            "save_data: dropna removed %d rows where Title or Price is null:\n%s",
            na_removed,
            dropped.to_string(),
        )

    log.info("save_data: writing %d rows to %s", len(df_no_na), output_file)
    df_no_na.to_excel(output_file, index=False)
    log.debug("save_data: write complete")
    return len(df_no_na)


def get_file_name():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"autotrader_info_{timestamp}.xlsx"
    return filename


def main():
    global driver, wait
    log.info("Starting AutoTrader scraper")
    options = Options()
    # 'eager' returns as soon as DOMContentLoaded fires — does not wait for ads,
    # tracking pixels, and other third-party resources that can hang indefinitely.
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
