"""P7A: SSRF-safe fetch guard."""
import pytest

from app.services.tools.ssrf_guard import SsrfBlockedError, _validate_host, _validate_url, safe_get


class _FakeResponse:
    """Minimal streaming response surface used by safe_get's streamed path:
    status/headers for redirect hops, iter_bytes() for body chunks."""

    def __init__(self, status_code, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def iter_bytes(self):
        if self._body:
            yield self._body


def _patch_client(monkeypatch, respond):
    """Install a fake httpx.Client whose .stream() delegates to `respond(url)`
    and records (url, headers) for every hop. Returns the recorded calls."""
    import httpx as httpx_module

    calls: list[tuple[str, dict | None]] = []

    class _FakeStream:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, headers=None):
            calls.append((url, headers))
            return _FakeStream(respond(url))

    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    return calls


def test_blocks_loopback_host():
    with pytest.raises(SsrfBlockedError):
        _validate_host("127.0.0.1")


def test_blocks_metadata_link_local_host():
    with pytest.raises(SsrfBlockedError):
        _validate_host("169.254.169.254")


def test_blocks_private_range_host():
    with pytest.raises(SsrfBlockedError):
        _validate_host("10.0.0.5")


def test_blocks_non_http_scheme():
    with pytest.raises(SsrfBlockedError):
        _validate_url("file:///etc/passwd")


def test_blocks_disallowed_port():
    with pytest.raises(SsrfBlockedError):
        _validate_url("https://example.com:5432/")


def test_blocks_unresolvable_host():
    with pytest.raises(SsrfBlockedError):
        _validate_host("this-host-does-not-exist.invalid")


def test_safe_get_rejects_oversized_response(monkeypatch):
    class _Oversized:
        status_code = 200
        headers: dict = {}

        def iter_bytes(self):
            # many small chunks totalling 30 bytes > max_bytes=10
            for _ in range(6):
                yield b"x" * 5

    _patch_client(monkeypatch, lambda url: _Oversized())
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    with pytest.raises(SsrfBlockedError):
        safe_get("https://example.com/", timeout_seconds=5, max_bytes=10)


def test_safe_get_rejects_too_many_redirects(monkeypatch):
    _patch_client(monkeypatch,
                  lambda url: _FakeResponse(302, {"location": "https://example.com/next"}))
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    with pytest.raises(SsrfBlockedError):
        safe_get("https://example.com/", timeout_seconds=5, max_bytes=1_000_000)


def test_safe_get_drops_headers_on_cross_origin_redirect(monkeypatch):
    """A cross-origin hop must never receive the previous origin's headers —
    the guard validates network destinations, not credential trust."""
    calls = _patch_client(monkeypatch, lambda url: (
        _FakeResponse(302, {"location": "https://attacker.example.com/collect"})
        if url == "https://search.example.com/v1" else _FakeResponse(200, body=b"ok")))
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    response = safe_get("https://search.example.com/v1", timeout_seconds=5, max_bytes=100,
                        headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert calls[0] == ("https://search.example.com/v1", {"Authorization": "Bearer secret"})
    assert calls[1] == ("https://attacker.example.com/collect", None)


def test_safe_get_keeps_headers_on_same_origin_redirect(monkeypatch):
    calls = _patch_client(monkeypatch, lambda url: (
        _FakeResponse(302, {"location": "/results"})
        if url == "https://search.example.com/v1" else _FakeResponse(200, body=b"ok")))
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    response = safe_get("https://search.example.com/v1", timeout_seconds=5, max_bytes=100,
                        headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert calls[1] == ("https://search.example.com/results", {"Authorization": "Bearer secret"})


def test_safe_get_header_drop_is_one_way_latch(monkeypatch):
    """Once headers are dropped on a cross-origin hop they stay dropped, even
    when the attacker's host then redirects to its OWN origin (which would
    otherwise re-attach the credential)."""
    def _respond(url):
        if url == "https://search.example.com/v1":
            return _FakeResponse(302, {"location": "https://attacker.example.com/a"})
        if url == "https://attacker.example.com/a":
            return _FakeResponse(302, {"location": "https://attacker.example.com/collect"})
        return _FakeResponse(200, body=b"ok")

    calls = _patch_client(monkeypatch, _respond)
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    response = safe_get("https://search.example.com/v1", timeout_seconds=5, max_bytes=100,
                        headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert calls[0] == ("https://search.example.com/v1", {"Authorization": "Bearer secret"})
    assert calls[1] == ("https://attacker.example.com/a", None)
    assert calls[2] == ("https://attacker.example.com/collect", None)  # latch holds
