import argparse
import json
import os
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, sync_playwright
from requests.exceptions import RequestException

"""
This script is designed to crawl websites in a manner closely approximating real human browsing.
It uses Playwright to render pages with JavaScript and lazy loading, captures screenshots,
extracts HTML, compiles a JSON-based site structure, and compares discovered pages against
the site's official sitemap.

Key features and recent enhancements:
1. Type Hints: Clarify interfaces for static analysis.
2. Enhanced Error Handling: Retries for sitemap fetch, handling infinite scrolling, slow JS, and network/server errors.
3. YAML Config: Previously hard-coded parameters (scroll attempts, timeouts, attempts) now controlled by a YAML file.
4. Verbose Documentation: Comprehensive inline comments and docstrings to aid LLMs and engineers.
5. ESG Considerations: Reflects on environmental, social, and governance implications of improved site transparency.

Usage examples and CLI arguments are detailed below and in the README.

This version has been updated to generic references (e.g., apple.com) to
avoid mentioning any specific private domains.
"""

# Default configuration values, overridden by YAML if provided.
DEFAULT_CONFIG = {
    "headless": False,
    "scroll_wait_seconds": 2,
    "max_scroll_attempts": 10,
    "image_load_attempts": 3,
    "image_load_attempt_delay": 2,
    "network_timeout_seconds": 30,
    "sitemap_request_retries": 3,
    "sitemap_request_delay": 3,
}


def load_config(settings_file: Optional[str]) -> Dict[str, object]:
    """
    Load configuration from a YAML file if provided, otherwise use defaults.
    If YAML is malformed or not a dict, raise ValueError.

    This enables operators to tailor crawling behavior without code changes.
    """
    if settings_file and os.path.exists(settings_file):
        with open(settings_file, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
            if not isinstance(user_config, dict):
                raise ValueError("Config file must define a dictionary of settings.")
            # Merge user settings into defaults, user settings take precedence
            final_config = {**DEFAULT_CONFIG, **user_config}
            return final_config
    return DEFAULT_CONFIG.copy()


def is_internal_link(base: str, link: str) -> bool:
    """
    Determine if 'link' is internal to the base URL domain.

    Internal means same domain or relative path.
    Prevents unintended large-scale external crawling.
    """
    if not link:
        return False
    parsed_link = urlparse(link)
    parsed_base = urlparse(base)
    return (parsed_link.netloc == parsed_base.netloc) or (parsed_link.netloc == "")


def normalize_sitemap_url(
    raw_url: str,
    real_domain: str,
    domain_fix_regex: Optional[str] = None,
    domain_fix_replacement: Optional[str] = None,
) -> str:
    """
    Normalize sitemap URLs by applying a regex replacement if provided.

    Useful for 'dodgy' sitemaps referencing incorrect domains.
    """
    if domain_fix_regex and domain_fix_replacement:
        try:
            raw_url = re.sub(domain_fix_regex, domain_fix_replacement, raw_url)
        except re.error as e:
            raise ValueError(f"Invalid regex for domain fixing: {str(e)}")
    return raw_url


def parse_sitemap(
    sitemap_url: str,
    real_domain: str,
    domain_fix_regex: Optional[str],
    domain_fix_replacement: Optional[str],
    config: Dict[str, object],
) -> Set[str]:
    """
    Fetch and parse the sitemap.xml, retrying if network errors occur.

    Returns a set of normalized URLs found in the sitemap.
    If no successful fetch, returns empty set.
    """
    sitemap_urls: Set[str] = set()
    attempts = config.get("sitemap_request_retries", 3)
    delay = config.get("sitemap_request_delay", 3)

    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(
                sitemap_url, timeout=config.get("network_timeout_seconds", 30)
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "xml")
                for loc_tag in soup.find_all("loc"):
                    raw_url = loc_tag.text.strip()
                    normalized_url = normalize_sitemap_url(
                        raw_url, real_domain, domain_fix_regex, domain_fix_replacement
                    )
                    sitemap_urls.add(normalized_url.rstrip("/"))
                return sitemap_urls
            else:
                print(
                    f"Warning: Received HTTP {resp.status_code} for sitemap. "
                    f"Attempt {attempt}/{attempts}."
                )
        except (RequestException, ValueError) as e:
            print(f"Error fetching sitemap: {e}. Attempt {attempt}/{attempts}")
        time.sleep(delay)

    print("Warning: Unable to retrieve or parse sitemap.xml after multiple attempts.")
    return sitemap_urls


def scroll_to_bottom(page: Page, config: Dict[str, object]) -> None:
    """
    Simulate user-like scrolling to trigger lazy loading.
    Stop when no new content appears or max attempts reached.
    """
    previous_height = 0
    max_attempts = config.get("max_scroll_attempts", 10)
    wait_seconds = config.get("scroll_wait_seconds", 2)

    for _ in range(max_attempts):
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(wait_seconds)


def ensure_all_images_loaded(page: Page, config: Dict[str, object]) -> None:
    """
    Ensure images are fully loaded before screenshot.

    Tries image_load_attempts times, waiting image_load_attempt_delay seconds.
    """
    attempts = config.get("image_load_attempts", 3)
    delay = config.get("image_load_attempt_delay", 2)

    for _ in range(attempts):
        all_loaded = page.evaluate(
            """
            () => {
                const imgs = [...document.querySelectorAll('img')];
                if (imgs.length === 0) return true;
                return imgs.every(img => img.complete && img.naturalWidth > 0);
            }
            """
        )
        if all_loaded:
            return
        time.sleep(delay)


def safe_filename(url: str) -> str:
    """
    Convert a URL into a filesystem-safe filename by replacing special characters.
    """
    name = url.replace("https://", "").replace("http://", "")
    for ch in ["/", "?", "&", ":"]:
        name = name.replace(ch, "_")
    return name


def record_page_structure(
    structure: Dict[str, dict],
    url: str,
    parent_url: Optional[str],
    link_text: Optional[str],
) -> None:
    """
    Record how we reached a particular URL.

    "reached_from": list of (parent_url, link_text).
    """
    if url not in structure:
        structure[url] = {
            "url": url,
            "reached_from": [],
        }
    if parent_url:
        structure[url]["reached_from"].append((parent_url, link_text))


def crawl_page(
    page: Page,
    url: str,
    structure: Dict[str, dict],
    visited: Set[str],
    output_dir: str,
    html_dir: str,
    config: Dict[str, object],
) -> List[Tuple[str, str]]:
    """
    Crawl a single page:
    - Navigate with timeout
    - Scroll & load images
    - Capture title, screenshot, HTML
    - Extract internal links
    """
    try:
        page.goto(
            url,
            wait_until="networkidle",
            timeout=config.get("network_timeout_seconds", 30) * 1000,
        )
    except Exception as e:
        print(f"Error navigating to {url}: {e}")
        return []

    scroll_to_bottom(page, config)
    ensure_all_images_loaded(page, config)

    title: str = page.title()

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(html_dir, exist_ok=True)

    base_filename = safe_filename(url)
    screenshot_path = os.path.join(output_dir, base_filename + ".png")
    html_path = os.path.join(html_dir, base_filename + ".html")

    # Attempt screenshot
    try:
        page.screenshot(path=screenshot_path, full_page=True)
    except Exception as e:
        print(f"Error taking screenshot of {url}: {e}")

    # Attempt HTML save
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        print(f"Error saving HTML for {url}: {e}")

    # Update site structure
    if url in structure:
        structure[url]["title"] = title
        structure[url]["screenshot"] = screenshot_path
        structure[url]["html_file"] = html_path
    else:
        structure[url] = {
            "url": url,
            "reached_from": [],
            "title": title,
            "screenshot": screenshot_path,
            "html_file": html_path,
        }

    # Extract links
    links_locator = page.locator("a[href]")
    count = links_locator.count()
    found_links: List[Tuple[str, str]] = []
    for i in range(count):
        href = links_locator.nth(i).get_attribute("href")
        link_txt = (
            links_locator.nth(i).inner_text().strip()
            if links_locator.nth(i).inner_text()
            else ""
        )
        if href:
            full_link = urljoin(url, href).rstrip("/")
            if is_internal_link(url, full_link) and full_link not in visited:
                found_links.append((full_link, link_txt))

    return found_links


def crawl_site(
    start_url: str,
    domain_fix_regex: Optional[str],
    domain_fix_replacement: Optional[str],
    config: Dict[str, object],
) -> None:
    """
    Crawl an entire site starting from start_url:
    - Parse sitemap for comparison
    - BFS-like approach to discover internal pages
    - Save structure & diff to JSON
    """
    parsed = urlparse(start_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = urljoin(base_domain, "sitemap.xml")

    visited: Set[str] = set()
    structure: Dict[str, dict] = {}

    screenshot_dir = f"screenshots_{parsed.netloc}"
    html_dir = f"html_{parsed.netloc}"

    # Parse sitemap
    sitemap_urls = parse_sitemap(
        sitemap_url, base_domain, domain_fix_regex, domain_fix_replacement, config
    )

    headless_mode = config.get("headless", False)
    try:
        with sync_playwright() as p:
            browser: Browser = p.chromium.launch(headless=headless_mode)
            page: Page = browser.new_page()

            queue: List[Tuple[str, Optional[str], Optional[str]]] = [
                (start_url, None, None)
            ]
            while queue:
                url, parent_url, link_txt = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                record_page_structure(structure, url, parent_url, link_txt)
                found_links = crawl_page(
                    page, url, structure, visited, screenshot_dir, html_dir, config
                )
                for l, txt in found_links:
                    if l not in visited:
                        queue.append((l, url, txt))

            browser.close()
    except Exception as e:
        print(f"Error running the browser or crawling: {e}")

    discovered_urls = set(structure.keys())

    urls_in_sitemap_not_in_crawl = sitemap_urls - discovered_urls
    urls_in_crawl_not_in_sitemap = discovered_urls - sitemap_urls

    site_structure_filename = f"site_structure_{parsed.netloc}.json"
    with open(site_structure_filename, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    diff = {
        "urls_in_sitemap_not_in_crawl": list(urls_in_sitemap_not_in_crawl),
        "urls_in_crawl_not_in_sitemap": list(urls_in_crawl_not_in_sitemap),
    }
    sitemap_diff_filename = f"sitemap_diff_{parsed.netloc}.json"
    with open(sitemap_diff_filename, "w", encoding="utf-8") as f:
        json.dump(diff, f, indent=2, ensure_ascii=False)

    print(f"Crawl complete for {start_url}.")
    print(
        f"Site structure saved to {site_structure_filename}, diffs in {sitemap_diff_filename}."
    )
    print(f"Screenshots in {screenshot_dir}/, HTML pages in {html_dir}/")


def main() -> None:
    """
    CLI entry point.

    Supports:
    - Single site (--url)
    - Multiple sites (--config)
    - Domain fix regex/replacements
    - YAML settings file (--settings-file)

    Example:
    poetry run python main.py --url https://apple.com --settings-file settings.yaml
    """
    parser = argparse.ArgumentParser(
        description=(
            "Crawl websites realistically (JS-enabled, lazy-loading), capture screenshots, page titles, HTML, "
            "build a JSON site structure, and diff against sitemap.xml. "
            "Use --domain-fix-regex and --domain-fix-replacement to normalize dodgy sitemap domains. "
            "Use --settings-file for YAML config overriding defaults."
        )
    )
    parser.add_argument("--url", help="Single site to crawl.")
    parser.add_argument("--config", help="JSON config with multiple sites.")
    parser.add_argument(
        "--domain-fix-regex", help="Regex pattern to fix dodgy sitemap URLs."
    )
    parser.add_argument(
        "--domain-fix-replacement", help="Replacement string for dodgy sitemap URLs."
    )
    parser.add_argument(
        "--settings-file",
        help="YAML settings file to override default crawling parameters.",
    )

    args = parser.parse_args()

    if args.url and args.config:
        raise ValueError("Specify either --url or --config, not both.")

    config = load_config(args.settings_file)

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        sites = config_data.get("urls", [])
        if not sites:
            raise ValueError("No URLs found in config file.")
    elif args.url:
        sites = [args.url]
    else:
        parser.error("You must specify either --url or --config")

    for site in sites:
        crawl_site(
            site,
            domain_fix_regex=args.domain_fix_regex,
            domain_fix_replacement=args.domain_fix_replacement,
            config=config,
        )


if __name__ == "__main__":
    main()
