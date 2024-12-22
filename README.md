Below is a **merged** version of your README that reflects your “correct” text **plus** the relevant clarifications and details that were in the newer version. It should capture **all** important points from both sources:

---

# Web Crawl Screenshot and Sitemap Diff Tool

[![CI Tests](https://img.shields.io/badge/CI-Tests%20Passing-green)](#)
*(Hypothetical CI status badge; integrate with your CI pipeline for actual status)*

## Overview

This tool crawls one or more websites in a manner closely approximating real user browsing. It captures **screenshots** and **HTML** for each discovered page, then **compares** the actual navigable pages to each site’s `sitemap.xml` to highlight discrepancies.

**Key Highlights**:
- **BFS Crawler** that explores internal “content” links (skipping nav/footer/mailto/`javascript:void(0)`, etc.).
- **Screenshots & HTML** saved for each discovered page.
- **Timestamped Output** prevents overwriting old runs.
- **Configurable** via YAML (headless mode, domain fixes, timeouts, etc.).
- **Log** is written to both console and a timestamped file in `crawl_output/<timestamp>/`.

---

## Installation

**Prerequisites**:
- Python **3.11.x**
- [Poetry](https://python-poetry.org/)
- System dependencies for [Playwright](https://playwright.dev/) (e.g., `poetry run playwright install`).

1. **Install**:
   ```bash
   poetry install
   poetry run playwright install
   ```
   This sets up all dependencies and downloads the necessary browsers (e.g., Chromium).

2. **Folder Structure**:
   - `web_crawl_screenshot/`:
     - `main.py`: BFS logic, sitemap diffs, domain fixes, etc.
   - `tests/`:
     - `test_main.py`: Comprehensive tests using `pytest`.

---

## Usage

### Single-Site Crawl

To crawl **one** site (e.g., `https://apple.com`):

```bash
poetry run python -m web_crawl_screenshot.main --url https://apple.com
```

**Result**:  
- A timestamped folder `crawl_output/<timestamp>/apple.com/` is created.  
- Inside it:
  - `screenshots/`, `html/`, plus JSON files: `site_structure_apple.com.json` and `sitemap_diff_apple.com.json`.  
  - A log file named `web_crawl_log_<timestamp>.txt` appears in `crawl_output/<timestamp>/`.

### Multiple-Site Crawl

If you have a JSON file (`config.json`) like:

```json
{
  "urls": [
    "https://apple.com",
    "https://developer.apple.com"
  ]
}
```

then run:

```bash
poetry run python -m web_crawl_screenshot.main --config config.json
```

**Result**:  
- Each domain (`apple.com`, `developer.apple.com`) gets its own subfolder under `crawl_output/<timestamp>/`.

---

## Dodgy Sitemaps

Some sitemaps reference staging domains (e.g., `my-frontend-app.azurewebsites.net`) instead of your real domain (e.g., `apple.com`). You can **fix** those references via a YAML config, for **either** single or multiple sites. For example:

```yaml
headless: false
max_scroll_attempts: 15
domain_fixes:
  - match_domain: apple.com
    fix_rules:
      - regex: "https://apple-frontend-app\\.azurewebsites\\.net"
        replacement: "https://apple.com"
  - match_domain: developer.apple.com
    fix_rules:
      - regex: "https://dev-frontend\\.azurewebsites\\.net"
        replacement: "https://developer.apple.com"
```

### Single-Site Example with Dodgy Sitemaps

```bash
poetry run python -m web_crawl_screenshot.main \
  --url https://apple.com \
  --settings-file my_settings.yaml
```

### Multi-Site Example with Dodgy Sitemaps

```bash
poetry run python -m web_crawl_screenshot.main \
  --config config.json \
  --settings-file my_settings.yaml
```

In either scenario, the crawler parses your `my_settings.yaml`, sees any “dodgy” references in each sitemap, and normalizes them accordingly.

---

## Why Headless=False?

By default, you might see something like:

```yaml
headless: false
```

in your YAML config. This is often **recommended** for sites with heavy JavaScript or lazy-loading, because running in a **non-headless** (visible) mode can help ensure you **accurately render** dynamic elements before the screenshot is taken. It’s also great for **debugging**: you’ll see the Playwright browser window open and can confirm whether a site’s dynamic features are fully loaded.

For production or CI, where performance is paramount, you can switch to:

```yaml
headless: true
```

But be aware that **some sites may not** render identically in headless mode. If you notice missing sections or incomplete screenshots, revert to `headless: false`.

---

## Folder Structure Example

After crawling `https://apple.com` and `https://developer.apple.com` at 16:45 on December 22, 2024, you might see:

```
crawl_output/
└── 2024-12-22_16-45/
    ├── web_crawl_log_2024-12-22_16-45.txt
    ├── apple.com/
    │   ├── screenshots/
    │   │   └── apple.com_index.png
    │   ├── html/
    │   │   └── apple.com_index.html
    │   ├── site_structure_apple.com.json
    │   └── sitemap_diff_apple.com.json
    └── developer.apple.com/
        ├── screenshots/
        │   └── developer.apple.com_index.png
        ├── html/
        │   └── developer.apple.com_index.html
        ├── site_structure_developer.apple.com.json
        └── sitemap_diff_developer.apple.com.json
```

- **`web_crawl_log_2024-12-22_16-45.txt`**: Console/log output.  
- **`<domain>/screenshots/` & `html/`**: Collected page captures.  
- **`site_structure_<domain>.json`**: BFS-based link and title data.  
- **`sitemap_diff_<domain>.json`**: Compares sitemap references vs. what BFS found.

---

## .gitignore

You likely want to exclude the entire `crawl_output/` folder so logs, screenshots, and HTML aren’t committed:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/

# macOS
.DS_Store

# Crawler logs & artifacts
crawl_output/
```

---

## Testing

Run tests locally with:

```bash
poetry run pytest
```

**Key Checks**:
- BFS logic (mailto skipping, “content-only” link queue).
- Sticky nav fix (`scrollTo(0, 0)`).
- Domain fixes (in YAML).
- Basic CLI argument validation (must specify `--url` or `--config`).

---

## Future Enhancements

- **Comparing Two Crawls**: A script or LLM prompt to compare old vs. new runs for layout or discovered-page changes.  
- **Parallel BFS**: Speed up large-site crawling with concurrency.  
- **Enhanced Link Categorization**: Distinguish multiple nav bars or submenus.  

Enjoy your BFS-based web crawler, screenshots, and diffs against your sitemaps!