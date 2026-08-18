"""P7B: sandboxed Playwright adapter — fake browser, real controls."""
import pytest

from app.services.tools.playwright import PlaywrightError, browse_page
from app.services.tools.ssrf_guard import SsrfBlockedError


class _FakeRoute:
    def __init__(self, url):
        self.request = type("Req", (), {"url": url})()
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class _FakePage:
    def __init__(self, title="Page", url="https://docs.example.com/", text="hello", routes=None):
        self._title, self._url, self._text = title, url, text
        self.routes = routes or []

    def set_default_timeout(self, ms):
        self.timeout_ms = ms

    def route(self, pattern, handler):
        self.handler = handler

    def goto(self, url, wait_until=None):
        self.goto_url = url

    def title(self):
        return self._title

    @property
    def url(self):
        return self._url

    def evaluate(self, expr):
        return self._text


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.on_handlers = {}

    def new_page(self):
        return self._page

    def on(self, event, handler):
        self.on_handlers[event] = handler


class _FakeBrowser:
    def __init__(self, page):
        self._page = page

    def new_context(self, accept_downloads=False):
        assert accept_downloads is False  # downloads disabled at the context
        return _FakeContext(self._page)

    def close(self):
        self.closed = True


def _install_fake(monkeypatch, page):
    calls = []

    class _FakePlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __init__(self):
            self.chromium = type("C", (), {"launch": lambda *a, **k: _FakeBrowser(page)})()
            calls.append(("launch",))

    # browse_page imports sync_playwright locally (from playwright.sync_api
    # import sync_playwright), so the source module's attribute must be
    # patched for the fake to take effect without a real browser.
    monkeypatch.setattr("playwright.sync_api.sync_playwright", _FakePlaywright)
    return calls


def test_browse_page_blocks_non_allowed_domain(monkeypatch):
    monkeypatch.setattr("app.services.tools.playwright._validate_url", lambda url: None)
    with pytest.raises(PlaywrightError):
        browse_page(url="https://evil.example.com/x", allowed_domains=["docs.example.com"])


def test_browse_page_blocks_private_range_before_browser(monkeypatch):
    def _reject_private(url):
        raise SsrfBlockedError("SSRF_BLOCKED_TARGET:x:10.0.0.1")

    monkeypatch.setattr("app.services.tools.playwright._validate_url", _reject_private)
    with pytest.raises(PlaywrightError):
        browse_page(url="http://10.0.0.1/admin", allowed_domains=["10.0.0.1"])


def test_browse_page_renders_and_wraps_as_artifact(monkeypatch):
    monkeypatch.setattr("app.services.tools.playwright._validate_url", lambda url: None)
    page = _FakePage()
    _install_fake(monkeypatch, page)
    result = browse_page(url="https://docs.example.com/", allowed_domains=["example.com"])
    assert result["title"] == "Page"
    assert result["final_url"] == "https://docs.example.com/"
    assert result["artifact"].sanitized_content == "hello"


def test_subresource_requests_are_validated_and_aborted(monkeypatch):
    monkeypatch.setattr("app.services.tools.playwright._validate_url", lambda url: None)
    page = _FakePage()
    captured = {}

    def _route(pattern, handler):
        captured["handler"] = handler

    page.route = _route
    _install_fake(monkeypatch, page)
    browse_page(url="https://docs.example.com/", allowed_domains=["example.com", "10.0.0.1"])

    # a same-domain subresource continues
    same = _FakeRoute("https://docs.example.com/style.css")
    captured["handler"](same)
    assert same.continued is True
    assert same.aborted is False

    # a cross-domain subresource is aborted
    evil = _FakeRoute("https://evil.example.net/t.js")
    captured["handler"](evil)
    assert evil.aborted is True
    assert evil.continued is False

    # a private-range subresource is aborted by the SSRF guard even though
    # its domain is on the allowlist
    def _block_private(request_url):
        if "10.0.0.1" in request_url:
            raise SsrfBlockedError("SSRF_BLOCKED_TARGET:x:10.0.0.1")

    monkeypatch.setattr("app.services.tools.playwright._validate_url", _block_private)
    private = _FakeRoute("http://10.0.0.1/health")
    captured["handler"](private)
    assert private.aborted is True
    assert private.continued is False


def test_browse_page_caps_extraction(monkeypatch):
    monkeypatch.setattr("app.services.tools.playwright._validate_url", lambda url: None)
    page = _FakePage(text="x" * 1000)
    _install_fake(monkeypatch, page)
    with pytest.raises(PlaywrightError):
        browse_page(url="https://docs.example.com/", allowed_domains=["example.com"], max_bytes=100)
