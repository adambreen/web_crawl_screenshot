import json
import os
import re
import shutil
import tempfile
from typing import Dict, Generator, List, Tuple
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml
import requests_mock

from web_crawl_screenshot.main import (
    DEFAULT_CONFIG,
    load_config,
    is_internal_link,
    parse_sitemap,
    apply_fix_rules,
    crawl_page,
    main,
)

"""
Refreshed test suite for the updated BFS crawler.

Key Points:
1. We test "apply_fix_rules" instead of the old "normalize_sitemap_url".
2. parse_sitemap references domain_fixes from config, so we mock requests + config to ensure it pulls the correct fix rules.
3. BFS tests remain largely the same: mailto skipping, category links, sticky nav, spinner wait, CLI argument validations, etc.

If you previously tested domain-fix arguments via CLI, those are removed in this new code.
"""


@pytest.fixture(scope="module")
def temp_dir() -> Generator[str, None, None]:
    """
    Yields a fresh temp directory for each module, and cleans up afterwards.
    """
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_apply_fix_rules() -> None:
    """
    New function test: apply_fix_rules should apply multiple regex replacements in sequence.
    If no fix_rules, raw_loc remains unchanged.
    """
    raw_loc = "https://vdg-frontend-app.azurewebsites.net/faq"
    fix_rules = [
        {
            "regex": r"https://vdg-frontend-app\.azurewebsites\.net",
            "replacement": "https://veridocglobal.com",
        },
        {
            "regex": r"/faq$",
            "replacement": "/faqpage",
        },
    ]
    updated = apply_fix_rules(raw_loc, fix_rules)
    assert updated == "https://veridocglobal.com/faqpage"

    # No fix rules => no change
    unchanged = apply_fix_rules(raw_loc, [])
    assert unchanged == raw_loc


def test_is_internal_link() -> None:
    """
    Confirm internal link detection logic is correct with the new BFS code.
    """
    base = "https://veridocglobal.com"
    assert is_internal_link(base, "https://veridocglobal.com/page") is True
    assert is_internal_link(base, "/relative/path") is True
    assert is_internal_link(base, None) is False
    assert is_internal_link(base, "") is False
    assert is_internal_link(base, "https://vdg-frontend-app.azurewebsites.net") is False


@pytest.mark.parametrize("status_code", [200, 404, 500])
def test_parse_sitemap(requests_mock: requests_mock.Mocker, status_code: int) -> None:
    """
    parse_sitemap now uses domain_fixes from config. We test either 200 or error.
    If 200, we confirm apply_fix_rules is triggered. If non-200, we get empty set.
    """
    real_domain = "https://veridocglobal.com"
    sitemap_url = "https://veridocglobal.com/sitemap.xml"

    test_config = DEFAULT_CONFIG.copy()
    test_config["domain_fixes"] = [
        {
            "match_domain": "veridocglobal.com",
            "fix_rules": [
                {
                    "regex": r"https://vdg-frontend-app\.azurewebsites\.net",
                    "replacement": "https://veridocglobal.com",
                }
            ],
        }
    ]

    if status_code == 200:
        # We'll embed a loc that references the staging domain, ensuring fix is applied
        sitemap_content = """
        <urlset>
          <url><loc>https://vdg-frontend-app.azurewebsites.net/faq</loc></url>
          <url><loc>https://veridocglobal.com/products</loc></url>
        </urlset>
        """
        requests_mock.get(sitemap_url, text=sitemap_content, status_code=200)
        results = parse_sitemap(sitemap_url, real_domain, test_config)
        # The first loc should be replaced
        assert "https://veridocglobal.com/faq" in results
        # The second loc remains unchanged
        assert "https://veridocglobal.com/products" in results
    else:
        # Non-200 => parse_sitemap returns empty
        requests_mock.get(sitemap_url, status_code=status_code)
        results = parse_sitemap(sitemap_url, real_domain, test_config)
        assert len(results) == 0


def test_load_config(temp_dir: str) -> None:
    """
    Confirm we can load a YAML with domain_fixes etc.
    """
    config_yaml = {
        "headless": True,
        "domain_fixes": [
            {
                "match_domain": "veridocglobal.com",
                "fix_rules": [
                    {
                        "regex": r"https://vdg-frontend-app\.azurewebsites\.net",
                        "replacement": "https://veridocglobal.com",
                    }
                ],
            }
        ],
    }
    yaml_path = os.path.join(temp_dir, "test_settings.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(config_yaml, f)

    cfg = load_config(yaml_path)
    assert cfg["headless"] is True
    assert "domain_fixes" in cfg
    assert len(cfg["domain_fixes"]) == 1
    fix_item = cfg["domain_fixes"][0]
    assert fix_item["match_domain"] == "veridocglobal.com"


@patch("web_crawl_screenshot.main.sync_playwright")
def test_main_cli_no_args(
    mock_playwright: MagicMock,
    capfd: pytest.CaptureFixture,
) -> None:
    """
    We still require either --url or --config, not both nor neither => SystemExit.
    """
    test_args = ["main.py"]
    with patch("sys.argv", test_args):
        with pytest.raises(SystemExit):
            main()
    out, err = capfd.readouterr()
    assert "You must specify either --url or --config" in (out + err)


def test_main_cli_both_url_and_config() -> None:
    """
    If user passes both --url and --config => parser.error => SystemExit.
    """
    test_args = [
        "main.py",
        "--url",
        "https://veridocglobal.com",
        "--config",
        "sites.json",
    ]
    with patch("sys.argv", test_args), pytest.raises(SystemExit):
        main()


def test_main_cli_config_file_missing_urls() -> None:
    """
    If config JSON has no 'urls', we raise ValueError.
    """
    test_args = ["main.py", "--config", "fakeconfig.json"]
    fake_config = {"foo": "bar"}  # no 'urls' key
    with patch("sys.argv", test_args), patch(
        "builtins.open", mock_open(read_data=json.dumps(fake_config))
    ):
        with pytest.raises(ValueError) as e:
            main()
        assert "No URLs found in config file." in str(e.value)


# ---------------- BFS/Crawl Tests ----------------


@patch("web_crawl_screenshot.main.wait_for_ajax_load")
@patch("web_crawl_screenshot.main.scroll_to_bottom")
@patch("web_crawl_screenshot.main.ensure_all_images_loaded")
def test_crawl_page_mailto_skipped(
    mock_images: MagicMock,
    mock_scroll: MagicMock,
    mock_waitajax: MagicMock,
) -> None:
    """
    BFS queue should skip mailto links, skip external, only enqueue internal content.
    """
    from web_crawl_screenshot.main import crawl_page

    mock_page = MagicMock()
    structure = {}
    visited = set()
    config = DEFAULT_CONFIG.copy()

    # Let's define a_locator to return a mock with 3 links => mailto, internal, external
    def locator_side_effect(selector: str):
        # We'll handle "a[href]" => 3 results, everything else => 0
        loc_mock = MagicMock()
        if selector == "a[href]":
            loc_mock.count.return_value = 3

            def nth_side_effect(i: int):
                nth_link = MagicMock()
                if i == 0:
                    nth_link.get_attribute.return_value = "mailto:someone@example.com"
                    nth_link.inner_text.return_value = "MailTo"
                elif i == 1:
                    nth_link.get_attribute.return_value = (
                        "https://veridocglobal.com/internal"
                    )
                    nth_link.inner_text.return_value = "Internal Link"
                else:
                    nth_link.get_attribute.return_value = (
                        "https://anotherdomain.com/page"
                    )
                    nth_link.inner_text.return_value = "External"
                return nth_link

            loc_mock.nth.side_effect = nth_side_effect
        else:
            loc_mock.count.return_value = 0
        return loc_mock

    mock_page.locator.side_effect = locator_side_effect
    mock_page.evaluate.return_value = 100  # scrollHeight

    found = crawl_page(
        page=mock_page,
        url="https://veridocglobal.com",
        structure=structure,
        visited=visited,
        output_dir="screenshots_test",
        html_dir="html_test",
        config=config,
    )
    # BFS => only one link => the internal link
    assert len(found) == 1
    assert found[0] == ("https://veridocglobal.com/internal", "Internal Link")


def test_crawl_page_sticky_nav_and_scroll() -> None:
    """
    Ensure the code calls scrollTo(0,0) after lazy loading.
    We mock evaluate calls and confirm the final top scroll is invoked.
    """
    from web_crawl_screenshot.main import crawl_page

    mock_page = MagicMock()
    structure = {}
    visited = set()
    config = DEFAULT_CONFIG.copy()

    # we'll track evaluate calls
    def evaluate_side(script: str):
        if "document.body.scrollHeight" in script:
            return 200
        if "window.scrollTo(0, 0)" in script:
            return None
        return None

    mock_page.evaluate.side_effect = evaluate_side
    # 0 links => BFS queue empty
    mock_page.locator.return_value.count.return_value = 0

    crawl_page(
        page=mock_page,
        url="https://veridocglobal.com",
        structure=structure,
        visited=visited,
        output_dir="screenshots_test",
        html_dir="html_test",
        config=config,
    )

    calls = [str(c[0][0]) for c in mock_page.evaluate.call_args_list]
    assert any("window.scrollTo(0, 0)" in call for call in calls)


@patch("web_crawl_screenshot.main.sync_playwright")
@patch("web_crawl_screenshot.main.wait_for_ajax_load")
def test_crawl_page_awaits_spinner(
    mock_wait_ajax: MagicMock, mock_playwright: MagicMock
) -> None:
    """
    We just confirm wait_for_ajax_load is called, ignoring link extraction complexities.
    """
    from web_crawl_screenshot.main import crawl_page

    mock_page = MagicMock()
    mock_browser = MagicMock()
    mock_play = MagicMock()

    mock_play.__enter__.return_value = mock_play
    mock_play.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    mock_playwright.return_value = mock_play

    config = DEFAULT_CONFIG.copy()
    config["network_timeout_seconds"] = 5

    # no links
    mock_page.locator.return_value.count.return_value = 0

    structure = {}
    found_links = crawl_page(
        page=mock_page,
        url="https://veridocglobal.com",
        structure=structure,
        visited=set(),
        output_dir="screenshots_test",
        html_dir="html_test",
        config=config,
    )

    mock_wait_ajax.assert_called_once()
    assert found_links == []


def test_crawl_page_category_links() -> None:
    """
    We test that BFS only enqueues content links.
    nav & footer are recognized duplicates, so BFS sees content only.
    """
    from web_crawl_screenshot.main import crawl_page

    mock_page = MagicMock()
    structure: Dict[str, dict] = {}
    visited = set()
    config = DEFAULT_CONFIG.copy()

    # specialized mocks for each category
    mock_nav = MagicMock()
    mock_footer = MagicMock()
    mock_a = MagicMock()
    mock_button = MagicMock()

    mock_nav.count.return_value = 2
    mock_footer.count.return_value = 1
    mock_a.count.return_value = 5
    mock_button.count.return_value = 1

    def locator_side(selector: str):
        if selector == "header nav a[href]":
            return mock_nav
        elif selector == "footer a[href]":
            return mock_footer
        elif selector == "a[href]":
            return mock_a
        elif "button[onclick*='window.location']" in selector:
            return mock_button
        fallback = MagicMock()
        fallback.count.return_value = 0
        return fallback

    mock_page.locator.side_effect = locator_side

    # nav => 2 links
    def nav_nth(idx: int):
        nth_link = MagicMock()
        if idx == 0:
            nth_link.get_attribute.return_value = "https://veridocglobal.com/nav1"
            nth_link.inner_text.return_value = "Nav1"
        else:
            nth_link.get_attribute.return_value = "https://veridocglobal.com/nav2"
            nth_link.inner_text.return_value = "Nav2"
        return nth_link

    mock_nav.nth.side_effect = nav_nth

    # footer => 1 link
    def foot_nth(idx: int):
        nth_link = MagicMock()
        nth_link.get_attribute.return_value = "https://veridocglobal.com/footer"
        nth_link.inner_text.return_value = "Footer Link"
        return nth_link

    mock_footer.nth.side_effect = foot_nth

    # a[href] => total=5 => 2 duplicates (nav1, nav2), 1 duplicate (footer), 2 new content
    all_href_side_effects = [
        "https://veridocglobal.com/nav1",
        "https://veridocglobal.com/nav2",
        "https://veridocglobal.com/footer",
        "https://veridocglobal.com/content1",
        "https://veridocglobal.com/content2",
    ]
    all_text_side_effects = [
        "Nav1",
        "Nav2",
        "Footer Link",
        "Content1",
        "Content2",
    ]

    def a_nth(idx: int):
        nth_link = MagicMock()
        nth_link.get_attribute.return_value = all_href_side_effects[idx]
        nth_link.inner_text.return_value = all_text_side_effects[idx]
        return nth_link

    mock_a.nth.side_effect = a_nth

    # button => 1 => "window.location='https://veridocglobal.com/buttonLink'"
    def button_nth(idx: int):
        nth_link = MagicMock()
        nth_link.get_attribute.return_value = (
            "window.location='https://veridocglobal.com/buttonLink'"
        )
        nth_link.inner_text.return_value = "Button Link"
        return nth_link

    mock_button.nth.side_effect = button_nth

    # Evaluate => doc.body.scrollHeight
    mock_page.evaluate.side_effect = lambda script: (
        100 if "document.body.scrollHeight" in script else None
    )

    found = crawl_page(
        page=mock_page,
        url="https://veridocglobal.com",
        structure=structure,
        visited=visited,
        output_dir="screenshots_test",
        html_dir="html_test",
        config=config,
    )
    # BFS => only content1, content2, buttonLink => total 3
    # nav1/nav2/footer are duplicates from nav/footer => not BFS
    assert len(found) == 3

    # confirm structure => 2 nav, 1 foot, 3 content
    root_data = structure.get("https://veridocglobal.com", {})
    links_data = root_data.get("links", {})
    assert len(links_data["primary_navigation"]) == 2
    assert len(links_data["footer"]) == 1
    assert len(links_data["content"]) == 3
