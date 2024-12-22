import argparse
import datetime
import json
import logging
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
This script crawls one or multiple websites, capturing screenshots, HTML snapshots,
and comparing discovered pages to the official sitemap.xml for each domain.

Enhancements:
1. Python logging => logs to console & timestamped file.
2. BFS approach => only 'content' links are enqueued (skipping mailto, nav/footer duplicates).
3. Handling multiple domain-fix rules in a YAML 'domain_fixes' array (for "dodgy" sitemaps).
4. Output is timestamped under crawl_output/<timestamp>, ensuring each run is separate.

Example YAML snippet for domain fixes:
    domain_fixes:
      - match_domain: veridocglobal.com
        fix_rules:
          - regex: "https://vdg-frontend-app\\.azurewebsites\\.net"
            replacement: "https://veridocglobal.com"
      - match_domain: veridocsign.com
        fix_rules:
          - regex: "https://vdg-sign-frontend\\.azurewebsites\\.net"
            replacement: "https://veridocsign.com"

All BFS artifacts (screenshots, HTML, JSON, logs) appear under:
    crawl_output/<timestamp>/<domain>/
"""

# ------------- Default Config -------------
DEFAULT_CONFIG = {
    "headless": False,
    "scroll_wait_seconds": 2,
    "max_scroll_attempts": 10,
    "image_load_attempts": 3,
    "image_load_attempt_delay": 2,
    "network_timeout_seconds": 30,
    "sitemap_request_retries": 3,
    "sitemap_request_delay": 3,
    "domain_fixes": [],
}


def setup_logger(logfile_path: str) -> None:
    """
    Configure a root logger at INFO level, outputting to console and a specified logfile.
    Overwrites logfile if it exists.
    """
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(logfile_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Clear existing handlers if any
    root_logger.handlers = []
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


logger = logging.getLogger(__name__)


def load_config(settings_file: Optional[str]) -> Dict[str, object]:
    """
    Load config from a YAML file if provided, else return defaults.
    If the YAML is malformed or not a dict, raise ValueError.
    """
    if settings_file and os.path.exists(settings_file):
        with open(settings_file, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
            if not isinstance(user_config, dict):
                raise ValueError("Config file must define a dictionary of settings.")
            merged = {**DEFAULT_CONFIG, **user_config}
            return merged
    return DEFAULT_CONFIG.copy()


def is_internal_link(base: str, link: str) -> bool:
    """
    Return True if 'link' is internal (same domain or relative path).
    Example: base='https://veridocglobal.com', link='https://veridocglobal.com/faq'
    """
    if not link:
        return False
    parsed_link = urlparse(link)
    parsed_base = urlparse(base)
    return (parsed_link.netloc == parsed_base.netloc) or (parsed_link.netloc == "")


def apply_fix_rules(raw_loc: str, fix_rules: List[Dict[str, str]]) -> str:
    """
    Apply zero or more regex replacements to 'raw_loc' in sequence.
    Each fix rule is { 'regex': <pattern>, 'replacement': <string> }.
    """
    updated = raw_loc
    for frule in fix_rules:
        pattern = frule["regex"]
        repl = frule["replacement"]
        try:
            updated = re.sub(pattern, repl, updated)
        except re.error as e:
            logger.warning(f"Invalid regex '{pattern}': {e}")
    return updated


def parse_sitemap(
    sitemap_url: str,
    real_domain: str,
    config: Dict[str, object],
) -> Set[str]:
    """
    Fetch/parse sitemap.xml. If domain_fixes is provided for that domain, apply them.
    Return set of discovered loc's. Retry on network errors or non-200 status.
    """
    parsed = urlparse(real_domain)
    domain_only = parsed.netloc  # e.g. 'veridocglobal.com'

    # Find fix_rules for this domain
    fix_rules: List[Dict[str, str]] = []
    for item in config.get("domain_fixes", []):
        if item.get("match_domain") == domain_only:
            fix_rules = item.get("fix_rules", [])
            break

    sitemap_urls: Set[str] = set()
    attempts = config.get("sitemap_request_retries", 3)
    delay = config.get("sitemap_request_delay", 3)

    for attempt_idx in range(1, attempts + 1):
        try:
            resp = requests.get(
                sitemap_url, timeout=config.get("network_timeout_seconds", 30)
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "xml")
                for loc_tag in soup.find_all("loc"):
                    raw_loc = loc_tag.text.strip()
                    # apply domain fix rules
                    normalized = apply_fix_rules(raw_loc, fix_rules).rstrip("/")
                    sitemap_urls.add(normalized)
                return sitemap_urls
            else:
                logger.warning(
                    f"Sitemap HTTP {resp.status_code}. Attempt {attempt_idx}/{attempts}."
                )
        except (RequestException, ValueError) as e:
            logger.warning(
                f"Error fetching sitemap: {e}. Attempt {attempt_idx}/{attempts}"
            )
        time.sleep(delay)

    logger.warning("Unable to retrieve or parse sitemap.xml after multiple attempts.")
    return sitemap_urls


def wait_for_ajax_load(page: Page, config: Dict[str, object]) -> None:
    """
    Wait for known spinner (#loaderImage) to vanish. If it never appears, or doesn't vanish, proceed anyway.
    """
    timeout_ms = config.get("network_timeout_seconds", 30) * 1000
    try:
        page.wait_for_selector("#loaderImage", state="detached", timeout=timeout_ms)
    except:
        logger.debug("Spinner never appeared or didn't detach. Continuing.")


def scroll_to_bottom(page: Page, config: Dict[str, object]) -> None:
    """
    Repeatedly scroll down to trigger lazy loads, stopping if height no longer changes.
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
    Attempt multiple times to ensure images have loaded (img.complete && naturalWidth > 0).
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
    Convert a URL into a filesystem-safe filename by replacing slashes, question marks, etc.
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
    In 'structure', record how 'url' was discovered by BFS from (parent_url, link_text).
    """
    if url not in structure:
        structure[url] = {
            "url": url,
            "reached_from": [],
        }
    if parent_url:
        structure[url]["reached_from"].append((parent_url, link_text))


def extract_links(page: Page) -> Dict[str, List[Dict[str, str]]]:
    """
    Extract all a[href] plus button[onclick*='window.location'], categorize them into:
      - primary_navigation (header nav a[href])
      - footer (footer a[href])
      - content (everything else, minus duplicates from above)
    """
    # 1) Primary nav
    primary_nav_links: List[Dict[str, str]] = []
    nav_count = page.locator("header nav a[href]").count()
    for i in range(nav_count):
        href = page.locator("header nav a[href]").nth(i).get_attribute("href") or ""
        txt = page.locator("header nav a[href]").nth(i).inner_text() or ""
        primary_nav_links.append({"href": href.strip(), "text": txt.strip()})

    # 2) Footer
    footer_links_list: List[Dict[str, str]] = []
    foot_count = page.locator("footer a[href]").count()
    for i in range(foot_count):
        href = page.locator("footer a[href]").nth(i).get_attribute("href") or ""
        txt = page.locator("footer a[href]").nth(i).inner_text() or ""
        footer_links_list.append({"href": href.strip(), "text": txt.strip()})

    # 3) All a[href]
    all_links: List[Dict[str, str]] = []
    a_count = page.locator("a[href]").count()
    for i in range(a_count):
        href = page.locator("a[href]").nth(i).get_attribute("href") or ""
        txt = page.locator("a[href]").nth(i).inner_text() or ""
        all_links.append({"href": href.strip(), "text": txt.strip()})

    # 4) Button-based nav: window.location
    button_links: List[Dict[str, str]] = []
    butt_count = page.locator("button[onclick*='window.location']").count()
    for i in range(butt_count):
        onclick = (
            page.locator("button[onclick*='window.location']")
            .nth(i)
            .get_attribute("onclick")
            or ""
        )
        txt = (
            page.locator("button[onclick*='window.location']").nth(i).inner_text() or ""
        )
        match = re.search(r"window\.location\s*=\s*['\"](.*?)['\"]", onclick)
        if match:
            button_links.append({"href": match.group(1).strip(), "text": txt.strip()})

    # Convert nav/footer to sets
    primary_nav_set = {(lnk["href"], lnk["text"]) for lnk in primary_nav_links}
    footer_set = {(lnk["href"], lnk["text"]) for lnk in footer_links_list}

    # Add button links to all_links
    for b in button_links:
        all_links.append({"href": b["href"], "text": b["text"]})

    # content = all_links minus anything in primary_nav_set/footer_set
    content_links: List[Dict[str, str]] = []
    for lnk in all_links:
        pair = (lnk["href"], lnk["text"])
        if pair not in primary_nav_set and pair not in footer_set:
            content_links.append(lnk)

    return {
        "primary_navigation": primary_nav_links,
        "footer": footer_links_list,
        "content": content_links,
    }


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
    BFS-crawl a single page:
    - Goto => wait for spinner => scroll => screenshot => HTML => extract links => skip mailto => BFS from content only
    """
    logger.info(f"Visiting: {url}")
    try:
        page.goto(
            url,
            wait_until="networkidle",
            timeout=config.get("network_timeout_seconds", 30) * 1000,
        )
    except Exception as e:
        logger.warning(f"Error navigating to {url}: {e}")
        return []

    wait_for_ajax_load(page, config)
    scroll_to_bottom(page, config)
    ensure_all_images_loaded(page, config)

    page.evaluate("window.scrollTo(0, 0)")  # sticky nav fix

    title: str = page.title()

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(html_dir, exist_ok=True)

    base_filename = safe_filename(url)
    screenshot_path = os.path.join(output_dir, base_filename + ".png")
    html_path = os.path.join(html_dir, base_filename + ".html")

    # screenshot
    try:
        page.screenshot(path=screenshot_path, full_page=True)
    except Exception as e:
        logger.warning(f"Error taking screenshot of {url}: {e}")

    # HTML
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        logger.warning(f"Error saving HTML for {url}: {e}")

    # update BFS structure
    if url not in structure:
        structure[url] = {
            "url": url,
            "reached_from": [],
            "title": title,
            "screenshot": screenshot_path,
            "html_file": html_path,
        }
    else:
        structure[url]["title"] = title
        structure[url]["screenshot"] = screenshot_path
        structure[url]["html_file"] = html_path

    link_categories = extract_links(page)
    structure[url]["links"] = link_categories

    # BFS => only enqueue content links
    found_links: List[Tuple[str, str]] = []
    for lnk in link_categories["content"]:
        href = lnk["href"]
        txt = lnk["text"]
        # skip mailto
        if href.startswith("mailto:"):
            continue
        # BFS if internal, not visited
        if is_internal_link(url, href) and href not in visited:
            found_links.append((href, txt))

    logger.info(f"Found {len(found_links)} BFS links from content on {url}.")
    return found_links


def crawl_site(
    start_url: str,
    config: Dict[str, object],
    output_root: str,
) -> None:
    """
    BFS crawl from 'start_url'.
    1) parse sitemap (with domain_fixes)
    2) BFS => store screenshots, html, JSON
    3) Compare discovered vs. official sitemap => diff
    All artifacts saved in domain-specific subfolders under output_root.
    """
    parsed = urlparse(start_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    domain_folder = os.path.join(output_root, parsed.netloc)
    screenshots_dir = os.path.join(domain_folder, "screenshots")
    html_dir = os.path.join(domain_folder, "html")

    visited: Set[str] = set()
    structure: Dict[str, dict] = {}

    # parse official sitemap
    sitemap_url = urljoin(base_domain, "sitemap.xml")
    logger.info(f"Parsing sitemap => {sitemap_url}")
    sitemap_urls = parse_sitemap(sitemap_url, base_domain, config)

    headless = config.get("headless", False)
    try:
        with sync_playwright() as p:
            browser: Browser = p.chromium.launch(headless=headless)
            page: Page = browser.new_page()

            queue: List[Tuple[str, Optional[str], Optional[str]]] = [
                (start_url, None, None)
            ]
            while queue:
                current_url, parent_url, link_txt = queue.pop(0)
                if current_url in visited:
                    continue
                visited.add(current_url)

                record_page_structure(structure, current_url, parent_url, link_txt)
                new_links = crawl_page(
                    page,
                    current_url,
                    structure,
                    visited,
                    screenshots_dir,
                    html_dir,
                    config,
                )
                for link_url, link_text in new_links:
                    if link_url not in visited:
                        queue.append((link_url, current_url, link_text))

            browser.close()
    except Exception as e:
        logger.error(f"Fatal error crawling {start_url}: {e}")

    discovered_urls = set(structure.keys())
    missing_from_crawl = sitemap_urls - discovered_urls
    not_in_sitemap = discovered_urls - sitemap_urls

    os.makedirs(domain_folder, exist_ok=True)
    site_structure_filename = os.path.join(
        domain_folder, f"site_structure_{parsed.netloc}.json"
    )
    with open(site_structure_filename, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    diff_info = {
        "urls_in_sitemap_not_in_crawl": list(missing_from_crawl),
        "urls_in_crawl_not_in_sitemap": list(not_in_sitemap),
    }
    sitemap_diff_filename = os.path.join(
        domain_folder, f"sitemap_diff_{parsed.netloc}.json"
    )
    with open(sitemap_diff_filename, "w", encoding="utf-8") as f:
        json.dump(diff_info, f, indent=2, ensure_ascii=False)

    logger.info(f"Crawl complete for {start_url}.")
    logger.info(f"Site structure => {site_structure_filename}")
    logger.info(f"Sitemap diff => {sitemap_diff_filename}")
    logger.info(f"Screenshots => {screenshots_dir}/, HTML => {html_dir}/")


def main() -> None:
    """
    CLI entry point:
    - Single site => --url https://veridocglobal.com
    - Multiple sites => --config sites.json
    - YAML config => --settings-file settings.yaml (for domain_fixes, timeouts, etc.)
    """
    parser = argparse.ArgumentParser(
        description=(
            "BFS-crawl websites, capture screenshots/HTML, compare vs. sitemap.xml, and store output in timestamped folders."
        )
    )
    parser.add_argument("--url", help="Single site to crawl.")
    parser.add_argument("--config", help="JSON config with multiple sites.")
    parser.add_argument(
        "--settings-file",
        help="YAML file providing overrides (including domain_fixes for dodgy sitemaps).",
    )

    args = parser.parse_args()

    if args.url and args.config:
        parser.error("You must specify either --url or --config (not both).")

    # Create a unique timestamp for log + output
    now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    output_root = os.path.join("crawl_output", now)
    os.makedirs(output_root, exist_ok=True)

    # Setup logging
    log_path = os.path.join(output_root, f"web_crawl_log_{now}.txt")
    setup_logger(log_path)

    config = load_config(args.settings_file)

    # Determine site list
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

    # BFS for each site
    for site in sites:
        crawl_site(
            start_url=site,
            config=config,
            output_root=output_root,
        )

    logger.info(f"All crawling done. Log available at => {log_path}")


if __name__ == "__main__":
    main()
