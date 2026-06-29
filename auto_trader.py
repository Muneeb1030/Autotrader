import json
import logging
import re
import time
from datetime import datetime
from time import sleep
from urllib.parse import quote_plus

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
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
        listing_count = len(driver.find_elements(By.CSS_SELECTOR, 'ul[data-testid="desktop-search"] > li'))
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


def get_total_pages(postcode, make, model, fuel, year_from, year_to, output_file, page_start=1):
    try:
        url = "https://www.autotrader.co.uk/car-search"
        encoded_postcode = quote_plus(postcode)
        encoded_make = quote_plus(make)
        encoded_model = quote_plus(model) if model else ""
        encoded_fuel = quote_plus(fuel)

        model_param = f"&model={encoded_model}" if encoded_model else ""
        page_url = (
            f"{url}?fuel-type={encoded_fuel}&make={encoded_make}{model_param}&postcode={encoded_postcode}&year-from={year_from}&year-to={year_to}"
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

        log.info("Total pages for make=%r model=%r fuel=%r: %d", make, model, fuel, total_pages)

        for page_number in range(page_start, total_pages + 1):
            try:
                cars_data = []
                page_url = (
                    f"{url}?page={page_number}&fuel-type={fuel}&make={make}{model_param}&postcode={postcode}&year-from={year_from}&year-to={year_to}"
                )
                log.info("Fetching page %d / %d  — %s", page_number, total_pages, page_url)
                driver.get(page_url)

                # Wait for the listing container to appear before scrolling
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'ul[data-testid="desktop-search"]')))
                    log.debug("Listing container found on page %d", page_number)
                except Exception:
                    log.warning(
                        "Timed out waiting for 'desktop-search' list on page %d. Current URL: %s — page may not have loaded results.",
                        page_number,
                        driver.current_url,
                    )

                # Scroll to trigger lazy-loading of all listings
                scrolls = scroll_to_load_all()
                log.info("Page %d: scrolling complete (%d scrolls)", page_number, scrolls)

                page_html = driver.page_source
                soup = BeautifulSoup(page_html, "html.parser")

                # index 0 is a non-listing element (e.g. sponsored/header); skip it
                all_li = soup.select('ul[data-testid="desktop-search"] > li')
                log.debug(
                    "Page %d: 'desktop-search' list has %d <li> elements after full scroll",
                    page_number,
                    len(all_li),
                )
                if not all_li:
                    log.warning(
                        "Page %d: 'ul[data-testid=\"desktop-search\"]' returned 0 elements. The selector may have changed. Page title: %r",
                        page_number,
                        soup.title.text if soup.title else "N/A",
                    )
                    log.debug(
                        "Page %d HTML snippet (first 3000 chars):\n%s",
                        page_number,
                        page_html[:3000],
                    )

                li_elements = all_li[1:]  # skip first non-listing element
                log.debug("Page %d: processing %d listing elements", page_number, len(li_elements))

                for idx, li_element in enumerate(li_elements):
                    title = get_title(li_element)
                    price_text = get_price(li_element)
                    year, miles, fuel_type = get_other_car_data(li_element)

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
        log.exception("Fatal error in get_total_pages (make=%r model=%r fuel=%r): %s", make, model, fuel, e)


def get_make_data():
    """Return list of (uriValue, model, fuelType) tuples from make.json.

    ``model`` and ``fuelType`` default to empty string if not set.
    """
    with open("make.json", "r") as f:
        json_list = json.load(f)

    entries = [(entry["uriValue"], entry.get("model", ""), entry.get("fuelType", "")) for entry in json_list][::-1]
    log.debug("get_make_data: loaded %d make/model entries", len(entries))
    return entries


def get_other_car_data(li_element):
    ul_ele = li_element.select('ul[data-testid="search-listing-specs"]')
    year = miles = fuel_type = "NA"
    if not ul_ele:
        log.debug("get_other_car_data: no specs list found in li element")
        return year, miles, fuel_type
    for li_ele in ul_ele:
        li_items_sub = li_ele.select("li")
        if not li_items_sub:
            log.debug("get_other_car_data: specs ul has no <li> children")
            continue
        li_text_combined = " ".join(li.text for li in li_items_sub)
        log.debug("get_other_car_data: specs text: %r", li_text_combined)
        pattern = r"(\d+(?:,\d{3})?\s*mile(?:s)?)\s*.*?(\b" + r"\b|\b".join(map(re.escape, _FUEL_TYPE_NAMES)) + r"\b)"
        match = re.search(pattern, li_text_combined)
        year = li_items_sub[0].text
        if match:
            miles = match.group(1)
            fuel_type = match.group(2)
        else:
            log.debug(
                "get_other_car_data: miles/fuel regex did not match. Combined specs text: %r",
                li_text_combined,
            )
    return year, miles, fuel_type


def get_title(li_element):
    anchor_with_tag_id = li_element.select_one('a[data-testid="search-listing-title"]')
    if not anchor_with_tag_id:
        log.debug("get_title: anchor a[data-testid='search-listing-title'] not found")
        return None
    first_h3_child = anchor_with_tag_id.select_one("h3")
    if not first_h3_child:
        log.debug(
            "get_title: <h3> not found inside title anchor. Anchor HTML: %s",
            anchor_with_tag_id.prettify()[:300],
        )
        return None
    return first_h3_child.text


def get_price(li_element):
    price = li_element.select_one("span.at__sc-1mc7cl3-5.edXwbj")
    if not price:
        candidates = li_element.select("span[class*='price'], span[data-testid*='price']")
        log.debug(
            "get_price: selector 'span.at__sc-1mc7cl3-5.edXwbj' found nothing. Candidate price spans: %s",
            [str(c)[:120] for c in candidates],
        )
        return None
    return price.text.replace("£", "")


def get_config(filename):
    log.info("Loading config from: %s", filename)
    df = pd.read_csv(filename)
    log.info("Config rows: %d", len(df))
    output_file = get_file_name()
    log.info("Output file: %s", output_file)
    makes = get_make_data()
    log.info("Makes loaded: %d", len(makes))
    for index, row in df.iterrows():
        postal_code = row["PostalCode"]
        page_number = row["PageNumber"]
        year_from = row["year-from"]
        year_to = row["year-to"]
        for make, model, fuel_type in makes:
            log.info(
                "Row %d — postcode=%r make=%r model=%r fuel=%r years=%s–%s start_page=%s",
                index + 1,
                postal_code,
                make,
                model or "(all)",
                fuel_type,
                year_from,
                year_to,
                page_number,
            )
            sleep(1)
            get_total_pages(postal_code, make, model, fuel_type, year_from, year_to, output_file, page_number)
    log.info("Scrape complete. Output: %s", output_file)


def save_data(output_file, cars_data):
    if not cars_data:
        log.warning("save_data called with empty cars_data — nothing to save")
        return

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


def get_file_name():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"autotrader_info_{timestamp}.xlsx"
    return filename


def main():
    global driver, wait
    log.info("Starting AutoTrader scraper")
    driver = webdriver.Firefox()
    driver.maximize_window()
    wait = WebDriverWait(driver, 360)
    try:
        get_config(enter_path_of_config_file)
    finally:
        log.info("Quitting browser")
        driver.quit()
    log.info("DONE")


if __name__ == "__main__":
    main()
