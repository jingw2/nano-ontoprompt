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


def test_recognizes_google_custom_search_json_api_shape(monkeypatch):
    """A real Google Programmable Search Engine response uses `items[]` with
    `title`/`link`/`snippet` — not this adapter's own `results[]` shape. An
    admin who activates the connection against a real Google CSE endpoint
    must not silently get zero results back."""
    monkeypatch.setattr("app.services.tools.search.safe_get", lambda *a, **k: _FakeResponse(200, {
        "items": [{"title": "Google Result", "link": "https://example.com/g", "snippet": "from google"}],
    }))
    results = web_search(endpoint="https://www.googleapis.com/customsearch/v1", api_key="key", query="x")
    assert len(results) == 1
    assert results[0]["title"] == "Google Result"
    assert results[0]["url"] == "https://example.com/g"


def test_recognizes_serper_dev_shape(monkeypatch):
    """Serper.dev returns `organic[]` with `title`/`link`/`snippet`."""
    monkeypatch.setattr("app.services.tools.search.safe_get", lambda *a, **k: _FakeResponse(200, {
        "organic": [{"title": "Serper Result", "link": "https://example.com/s", "snippet": "from serper"}],
    }))
    results = web_search(endpoint="https://google.serper.dev/search", api_key="key", query="x")
    assert len(results) == 1
    assert results[0]["title"] == "Serper Result"
    assert results[0]["url"] == "https://example.com/s"


def test_recognizes_bing_web_search_v7_shape(monkeypatch):
    """Bing Web Search API v7 nests results under `webPages.value[]` with
    `name`/`url`/`snippet` (not `title`)."""
    monkeypatch.setattr("app.services.tools.search.safe_get", lambda *a, **k: _FakeResponse(200, {
        "webPages": {"value": [{"name": "Bing Result", "url": "https://example.com/b", "snippet": "from bing"}]},
    }))
    results = web_search(endpoint="https://api.bing.microsoft.com/v7.0/search", api_key="key", query="x")
    assert len(results) == 1
    assert results[0]["title"] == "Bing Result"
    assert results[0]["url"] == "https://example.com/b"


def test_bing_provider_sends_subscription_key_header_not_bearer(monkeypatch):
    """Bing Web Search API v7 rejects `Authorization: Bearer` — it requires
    `Ocp-Apim-Subscription-Key`. Using the generic adapter shape against a
    real Bing endpoint would 401 even with a correct key."""
    seen = {}

    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200, {"webPages": {"value": []}})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    web_search(endpoint="https://api.bing.microsoft.com/v7.0/search", api_key="bing-key",
               query="x", provider="bing")
    assert seen["headers"] == {"Ocp-Apim-Subscription-Key": "bing-key"}
    assert "Authorization" not in (seen["headers"] or {})


def test_google_provider_puts_key_in_query_param_not_a_header(monkeypatch):
    """Google Custom Search JSON API takes the key as `?key=`, not a header —
    and the endpoint already carries the required `cx` engine id, which this
    adapter must preserve (append with `&`, not overwrite)."""
    seen = {}

    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    web_search(endpoint="https://www.googleapis.com/customsearch/v1?cx=my-engine-id",
               api_key="google-key", query="x", provider="google")
    assert seen["headers"] is None  # no auth header at all
    assert "cx=my-engine-id" in seen["url"]
    assert "key=google-key" in seen["url"]
    assert seen["url"].count("?") == 1  # cx and key/q share one query string


def test_serper_provider_posts_a_json_body_with_x_api_key_header(monkeypatch):
    """Serper.dev is a POST with a JSON body and `X-API-KEY` — a GET with
    Bearer auth (the generic shape) would not reach the search index at
    all."""
    seen = {}

    def _fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json_body"] = json_body
        return _FakeResponse(200, {"organic": []})

    def _unexpected_get(*a, **k):
        raise AssertionError("serper must POST, not GET")

    monkeypatch.setattr("app.services.tools.search.safe_post", _fake_safe_post)
    monkeypatch.setattr("app.services.tools.search.safe_get", _unexpected_get)
    web_search(endpoint="https://google.serper.dev/search", api_key="serper-key",
               query="hello", result_limit=7, provider="serper")
    assert seen["url"] == "https://google.serper.dev/search"
    assert seen["headers"] == {"X-API-KEY": "serper-key"}
    assert seen["json_body"] == {"q": "hello", "num": 7}


def test_recognizes_brave_search_api_shape(monkeypatch):
    """Brave Search API nests results under `web.results[]` with
    `title`/`url`/`description` (not `snippet`)."""
    monkeypatch.setattr("app.services.tools.search.safe_get", lambda *a, **k: _FakeResponse(200, {
        "web": {"results": [{"title": "Brave Result", "url": "https://example.com/br", "description": "from brave"}]},
    }))
    results = web_search(endpoint="https://api.search.brave.com/res/v1/web/search", api_key="key", query="x")
    assert len(results) == 1
    assert results[0]["title"] == "Brave Result"
    assert results[0]["url"] == "https://example.com/br"


def test_brave_provider_sends_subscription_token_header_not_bearer(monkeypatch):
    """Brave Search API rejects `Authorization: Bearer` — it requires
    `X-Subscription-Token`."""
    seen = {}

    def _fake_safe_get(url, *, timeout_seconds, max_bytes, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200, {"web": {"results": []}})

    monkeypatch.setattr("app.services.tools.search.safe_get", _fake_safe_get)
    web_search(endpoint="https://api.search.brave.com/res/v1/web/search", api_key="brave-key",
               query="x", provider="brave")
    assert seen["headers"] == {"X-Subscription-Token": "brave-key"}
    assert "Authorization" not in (seen["headers"] or {})


def test_unrecognized_response_shape_returns_empty_not_an_error(monkeypatch):
    """A 200 with valid JSON that matches none of the known shapes degrades
    to zero results rather than raising — the health probe already caught a
    genuinely broken endpoint via the non-200/invalid-JSON paths."""
    monkeypatch.setattr("app.services.tools.search.safe_get",
                        lambda *a, **k: _FakeResponse(200, {"unexpected": "shape"}))
    assert web_search(endpoint="https://search.example.com", api_key=None, query="x") == []
