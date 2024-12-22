# Web Crawl Screenshot and Sitemap Diff Tool

[![CI Tests](https://img.shields.io/badge/CI-Tests%20Passing-green)](#)
*(Hypothetical CI status badge; integrate with your CI pipeline for actual status)*

## Overview

This tool crawls one or more websites in a manner closely approximating real user browsing. It captures **screenshots** and **HTML** for each discovered page, and then **compares** the actual navigable pages to the site’s `sitemap.xml` to highlight discrepancies.

**Key Highlights**:
- **BFS Crawler** that explores internal “content” links (skipping nav/footer/mailto).
- **Screenshots & HTML** saved for each page.
- **Timestamped Output** prevents overwriting old runs.
- **Configurable** via YAML (headless mode, domain fixes, timeouts, etc.).
- **Log** is written to console and a timestamped file in `crawl_output/<timestamp>/`.

---

## Installation

**Prerequisites**:
- Python 3.11.x
- [Poetry](https://python-poetry.org/)
- System dependencies for [Playwright](https://playwright.dev/) (e.g., `poetry run playwright install`).

1. **Install**:
   ```bash
   poetry install
   poetry run playwright install
   ```
   Installs Python dependencies and the necessary browsers (Chromium, etc.).

2. **Folder Structure**:
   - `web_crawl_screenshot/`:
     - `main.py`: BFS logic, domain fixes, logging, etc.
   - `tests/`:
     - `test_main.py`: Comprehensive tests using `pytest`.

---

## Usage

### Single-Site Crawl

If you have **one** target domain (e.g., `https://apple.com`):

```bash
poetry run python main.py --url https://apple.com
```

**Result**:  
- A folder named `crawl_output/<timestamp>/apple.com/` is created.  
- Inside, you’ll see `screenshots/`, `html/`, plus JSON files for site structure and sitemap diffs.  
- A log file named `web_crawl_log_<timestamp>.txt` also appears in `crawl_output/<timestamp>/`.

### Multiple-Site Crawl

If you have a JSON file (e.g., `config.json`) like this:

```json
{
  "urls": [
    "https://apple.com",
    "https://developer.apple.com"
  ]
}
```

Then run:

```bash
poetry run python main.py --config config.json
```

**Result**:  
- Each domain (`apple.com`, `developer.apple.com`) has its own subfolder within `crawl_output/<timestamp>/`.

---

## Dodgy Sitemaps

Some sitemaps might reference staging domains (e.g., `my-frontend-app.azurewebsites.net`) instead of your real domain (`apple.com`). You can **fix** those references via a YAML config, like this:

```yaml
headless: false  # or true if you prefer headless
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

Then run:

```bash
poetry run python main.py \
  --url https://apple.com \
  --settings-file my_settings.yaml
```

The crawler will parse `my_settings.yaml`, see any “dodgy” references in `sitemap.xml`, and normalize them accordingly.

---

## Why Headless=False?

By default, you might **see**:

```yaml
headless: false
```

in your YAML. This is ideal for **local debugging**—you’ll see the Playwright browser window open and pages load in real time. For **CI** or large-scale runs, set `headless: true` to run invisibly.

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

- **`web_crawl_log_2024-12-22_16-45.txt`**: All console messages.  
- **`<domain>/screenshots/` & `html/`**: Captures of each visited page.  
- **`site_structure_<domain>.json`**: BFS-based data about titles, links, etc.  
- **`sitemap_diff_<domain>.json`**: Highlights what the sitemap claims vs. what BFS discovered.

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
- BFS logic (mail-to skipping, content-only queue).
- Sticky nav fix (`scrollTo(0,0)`).
- Sitemaps with domain_fixes in YAML.
- Basic CLI argument validation (either `--url` or `--config`).

You can also integrate `pytest` with any CI provider to ensure reliability.

---

## Future Enhancements

- **Comparing Two Crawls**: Create a script or LLM prompt to compare old vs. new runs for changes in layout or discovered pages.  
- **Parallel BFS**: Speed up large-site crawling with concurrency.  
- **Enhanced Link Categorization**: Distinguish multiple nav bars or submenus.  

Enjoy your BFS-based web crawler, screenshots, and detailed diffs against your sitemaps!
