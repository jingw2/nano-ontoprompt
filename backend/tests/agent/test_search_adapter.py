"""P7A: Search adapter."""
import pytest

from app.services.tools.search import SearchError, web_search


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_rejects_missing_endpoint():
    with pytest.raises(SearchError):
        web_search(endpoint="", api_key=None, query="hello")


def test_rejects_empty_query():
    with pytest.raises(SearchError):
        web_search(endpoint="https://search.example.com", api_key=None, query="   ")


def test_wraps_results_as_untrusted_artifacts(monkeypatch):
    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        assert "q=ontoprompt" in url
        return _FakeResponse(200, {"results": [
            {"title": "OntoPrompt Docs", "url": "https://docs.example.com", "snippet": "<b>hi</b>"},
        ]})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    results = web_search(endpoint="https://search.example.com", api_key="key123", query="ontoprompt")
    assert len(results) == 1
    assert results[0]["title"] == "OntoPrompt Docs"
    assert results[0]["artifact"].sanitized_content == "hi"  # <b> stripped by Safe Markdown


def test_query_params_are_urlencoded(monkeypatch):
    from urllib.parse import quote_plus

    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        assert "q=" + quote_plus("A&B 财报") in url
        assert "count=5" in url
        assert "A&B" not in url  # the query's raw & is not a parameter separator
        return _FakeResponse(200, {"results": []})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    web_search(endpoint="https://search.example.com", api_key=None, query="A&B 财报")


def test_upstream_non_200_raises(monkeypatch):
    monkeypatch.setattr("app.services.tools.search.safe_get",
                        lambda *a, **k: _FakeResponse(500, {}))
    with pytest.raises(SearchError):
        web_search(endpoint="https://search.example.com", api_key=None, query="x")


def test_ssrf_block_is_wrapped_as_search_error(monkeypatch):
    from app.services.tools.ssrf_guard import SsrfBlockedError

    def _blocked(*a, **k):
        raise SsrfBlockedError("SSRF_BLOCKED_TARGET:x:10.0.0.1")

    monkeypatch.setattr("app.services.tools.search.safe_get", _blocked)
    with pytest.raises(SearchError):
        web_search(endpoint="https://search.example.com", api_key=None, query="x")


def test_result_limit_truncates(monkeypatch):
    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        return _FakeResponse(200, {"results": [
            {"title": f"r{i}", "url": f"https://x.example.com/{i}", "snippet": "s"} for i in range(10)
        ]})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    results = web_search(endpoint="https://search.example.com", api_key=None, query="x", result_limit=3)
    assert len(results) == 3
