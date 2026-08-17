"""P7A: SSRF-safe fetch guard."""
import pytest

from app.services.tools.ssrf_guard import SsrfBlockedError, _validate_host, _validate_url, safe_get


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
    import httpx as httpx_module

    class _FakeResponse:
        status_code = 200
        headers: dict = {}

        def read(self):
            return b"x" * 100

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _FakeResponse()

    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    with pytest.raises(SsrfBlockedError):
        safe_get("https://example.com/", timeout_seconds=5, max_bytes=10)


def test_safe_get_rejects_too_many_redirects(monkeypatch):
    import httpx as httpx_module

    class _FakeResponse:
        status_code = 302
        headers = {"location": "https://example.com/next"}

        def read(self):
            return b""

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _FakeResponse()

    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    monkeypatch.setattr("app.services.tools.ssrf_guard._validate_url", lambda url: None)
    with pytest.raises(SsrfBlockedError):
        safe_get("https://example.com/", timeout_seconds=5, max_bytes=1_000_000)
