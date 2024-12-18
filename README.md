# Web Crawl Screenshot and Sitemap Diff Tool

[![CI Tests](https://img.shields.io/badge/CI-Tests%20Passing-green)](#)
*(Hypothetical CI status badge; integrate with your CI pipeline for actual status)*

## Quick Start

1. **Install Dependencies**:  
   Requires Python 3.11.x and [Poetry](https://python-poetry.org/).
   ```bash
   poetry install
   poetry run playwright install
   ```

2. **Run a Single Site Crawl**:
   ```bash
   poetry run python main.py --url https://apple.com
   ```
   Captures screenshots, HTML, compares with sitemap.

3. **View Outputs**:
   - `site_structure_<domain>.json`: JSON of discovered pages.
   - `sitemap_diff_<domain>.json`: Differences from official sitemap.
   - `screenshots_<domain>/`: PNG screenshots of each visited page.
   - `html_<domain>/`: HTML files for offline analysis.

4. **Customizing Behavior**:
   Create a YAML settings file (e.g. `settings.yaml`):
   ```yaml
   headless: false
   max_scroll_attempts: 15
   image_load_attempts: 5
   network_timeout_seconds: 60
   ```
   Then run:
   ```bash
   poetry run python main.py --url https://apple.com --settings-file settings.yaml
   ```

## Overview

This tool simulates realistic user browsing:
- **JS & Lazy Loading**: Uses Playwright to fully render pages.
- **Data Capture**: Saves screenshots, HTML for UX, SEO, and content analysis.
- **Sitemap Comparison**: Checks actual navigable structure against `sitemap.xml`.

### ESG Considerations

- **Environmental**: Identifying redundant pages can reduce server load.
- **Social**: Insights into navigation improve UX and accessibility.
- **Governance**: Transparent comparison fosters trust and compliance.

## Multi-Site Crawling

For multiple sites:
```json
{
  "urls": ["https://apple.com", "https://example.com"]
}
```
```bash
poetry run python main.py --config config.json --settings-file settings.yaml
```

## Handling Dodgy Sitemaps

If `sitemap.xml` uses incorrect domains:
```bash
poetry run python main.py \
  --url https://apple.com \
  --domain-fix-regex "https://azure\.blob\.core\.windows\.net/apple/" \
  --domain-fix-replacement "https://apple.com/"
```

## CI/CD Integration

- Comprehensive tests (`test_main.py`) with mocks for stable, fast checks.
- Integrate `pytest` in CI pipelines.
- Status badge can indicate test pass/fail.

## Future Enhancements

- Integration tests against a local test server.
- Performance tuning for large sites.
- Future step: Comparing two crawl sessions and generating LLM-based reports on changes.

---
