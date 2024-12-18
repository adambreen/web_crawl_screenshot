import json
import os
import re
import shutil
import tempfile
from typing import Dict
from unittest.mock import MagicMock, call, mock_open, patch

import pytest
import yaml

# Import from main after formatting and linting improvements
from main import (
    DEFAULT_CONFIG,
    is_internal_link,
    load_config,
    main,
    normalize_sitemap_url,
    parse_sitemap,
)

"""
This test suite ensures that the web crawl tool works as expected.

Key checks:
- Internal link detection logic
- Domain normalization via regex
- Sitemap parsing under various HTTP responses
- Config loading from YAML
- CLI argument validation
- Basic crawling scenario with mocks

We've replaced references to the previous domain (veridocglobal.com) with
apple.com to keep this generic and suitable for public demonstration.

ESG: Good testing saves time and resources (Environmental), clarifies code for onboarding (Social),
and ensures compliance with coding standards (Governance).
"""


@pytest.fixture(scope="module")
def temp_dir():
    """Create a temporary directory for test artifacts and clean up afterward."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_is_internal_link():
    """Check internal link detection logic against various scenarios."""
    base = "https://apple.com"
    assert is_internal_link(base, "https://apple.com/page") is True
    assert is_internal_link(base, "http://apple.com/about") is True
    assert is_internal_link(base, "/relative/path") is True
    assert is_internal_link(base, "https://otherdomain.com") is False
    assert is_internal_link(base, None) is False
    assert is_internal_link(base, "") is False


def test_normalize_sitemap_url():
    """Verify domain normalization via regex replacement for dodgy sitemap URLs."""
    raw = "https://azure.blob.core.windows.net/apple/product.html"
    real_domain = "https://apple.com"
    # Without regex, no change
    assert normalize_sitemap_url(raw, real_domain) == raw

    regex = r"https://azure\.blob\.core\.windows\.net/apple/"
    replacement = "https://apple.com/"
    expected = "https://apple.com/product.html"
    assert normalize_sitemap_url(raw, real_domain, regex, replacement) == expected

    # Invalid regex should raise ValueError
    with pytest.raises(ValueError):
        normalize_sitemap_url(raw, real_domain, "[bad(regex", replacement)


@pytest.mark.parametrize("status_code", [200, 404, 500])
def test_parse_sitemap(requests_mock, status_code):
    """
    Test parse_sitemap under different HTTP responses.
    - 200: expect to parse URLs if valid XML.
    - Non-200: expect retries and eventually empty set.
    """
    real_domain = "https://apple.com"
    sitemap_url = "https://apple.com/sitemap.xml"
    config = DEFAULT_CONFIG.copy()

    if status_code == 200:
        sitemap_content = """
        <urlset>
          <url><loc>https://azure.blob.core.windows.net/apple/page1</loc></url>
          <url><loc>https://apple.com/page2</loc></url>
        </urlset>
        """
        requests_mock.get(sitemap_url, text=sitemap_content, status_code=200)
        results = parse_sitemap(
            sitemap_url,
            real_domain,
            r"https://azure\.blob\.core\.windows\.net/apple/",
            "https://apple.com/",
            config,
        )
        assert "https://apple.com/page1" in results
        assert "https://apple.com/page2" in results
    else:
        # Non-200 response, expect empty
        requests_mock.get(sitemap_url, status_code=status_code)
        results = parse_sitemap(sitemap_url, real_domain, None, None, config)
        assert len(results) == 0


def test_load_config(temp_dir):
    """
    Test loading a YAML config.
    Verify that user settings override defaults, invalid configs raise errors.
    """
    config_path = os.path.join(temp_dir, "settings.yaml")
    user_yaml = {
        "headless": True,
        "max_scroll_attempts": 20,
        "image_load_attempts": 5,
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(user_yaml, f)

    config = load_config(config_path)
    assert config["headless"] is True
    assert config["max_scroll_attempts"] == 20
    assert config["image_load_attempts"] == 5
    assert (
        config["network_timeout_seconds"] == DEFAULT_CONFIG["network_timeout_seconds"]
    )

    # Invalid YAML (not a dict)
    bad_path = os.path.join(temp_dir, "bad_settings.yaml")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_config(bad_path)


@patch("main.sync_playwright")
def test_main_cli_no_args(mock_playwright, capfd):
    """
    Test calling main without required arguments. Expect SystemExit.
    Ensures CLI argument validation still works.
    """
    test_args = ["main.py"]
    with patch("sys.argv", test_args):
        with pytest.raises(SystemExit):
            main()
    out, err = capfd.readouterr()
    # Expect error message about missing url or config
    assert "You must specify either --url or --config" in (out + err)


def test_main_cli_both_url_and_config():
    """
    Specifying both --url and --config should raise ValueError.
    Checks CLI logic correctness.
    """
    test_args = [
        "main.py",
        "--url",
        "https://apple.com",
        "--config",
        "sites.json",
    ]
    with patch("sys.argv", test_args), pytest.raises(ValueError) as e:
        main()
    assert "not both" in str(e.value)


def test_main_cli_config_file_missing_urls():
    """
    Config file with no 'urls' or empty urls list should raise ValueError.
    Ensures we fail early with helpful error messages.
    """
    test_args = ["main.py", "--config", "badconfig.json"]
    fake_config = {"urls": []}
    with patch("sys.argv", test_args), patch(
        "builtins.open", mock_open(read_data=json.dumps(fake_config))
    ):
        with pytest.raises(ValueError) as e:
            main()
        assert "No URLs found in config file." in str(e.value)


def test_invalid_domain_fix_regex():
    """
    Passing a non-compiling regex for domain fix should raise ValueError.
    """
    real_domain = "https://apple.com"
    with pytest.raises(ValueError):
        normalize_sitemap_url(
            "https://azure.blob.core.windows.net/apple/page",
            real_domain,
            "[bad(regex",
            "http://x.com",
        )
