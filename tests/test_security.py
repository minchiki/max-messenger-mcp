"""Проверки защиты без сети и без аккаунта MAX: python -m pytest tests (из корня репозитория)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("MAX_PUBLIC_URL", "http://localhost:8766")

import media  # noqa: E402
import oauth  # noqa: E402
import pages  # noqa: E402
import safety  # noqa: E402


@pytest.mark.parametrize("uri,ok", [
    ("https://chatgpt.com/connector_platform_oauth_redirect", True),
    ("https://claude.ai/api/mcp/auth_callback", True),
    ("http://localhost:33418/callback", True),
    ("http://127.0.0.1:6274/oauth/callback", True),
    ("cursor://anysphere.cursor-retrieval/oauth/callback", True),
    ("javascript:alert(document.domain)//", False),
    ("data:text/html,<script>alert(1)</script>", False),
    ("https://evil.example/cb", False),
    ("https://chatgpt.com.evil.example/cb", False),
    ("http://chatgpt.com/cb", False),
    ("file:///etc/passwd", False),
])
def test_redirect_allowlist(uri, ok):
    assert oauth.redirect_ok(uri) is ok


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "http://10.0.0.1/", "http://192.168.1.1/", "http://0x7f000001/", "file:///etc/passwd",
])
def test_ssrf_blocked(url):
    with pytest.raises(ValueError):
        asyncio.run(media.get(url))


def test_hidden_chars_removed():
    s = "при​вет‮\x00"
    assert safety.clean(s) == "привет"


def test_login_page_escapes_client_name():
    html = pages.login("pid123", '<img src=x onerror=alert(1)>', "chatgpt.com")
    assert "<img src=x" not in html and "&lt;img" in html
