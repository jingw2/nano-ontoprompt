"""P7A: Browser Use adapter."""
import pytest

from app.services.tools.browser_use import BrowserUseError, run_browser_task


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_rejects_missing_endpoint():
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="", api_key=None, task="do something")


def test_rejects_empty_task():
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="   ")


def test_posts_task_and_bearer_auth(monkeypatch):
    seen = {}

    def _fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        seen["url"] = url
        seen["json_body"] = json_body
        seen["headers"] = headers
        return _FakeResponse(200, {"output": "done", "url": "https://final.example.com"})

    monkeypatch.setattr("app.services.tools.browser_use.safe_post", _fake_safe_post)
    result = run_browser_task(endpoint="https://browser-use.example.com/run", api_key="key123",
                              task="find the price")
    assert seen["url"] == "https://browser-use.example.com/run"
    assert seen["json_body"] == {"task": "find the price"}
    assert seen["headers"] == {"Authorization": "Bearer key123"}
    assert result["content"] == "done"
    assert result["url"] == "https://final.example.com"
    assert result["artifact"].sanitized_content == "done"


def test_no_api_key_sends_no_auth_header(monkeypatch):
    seen = {}

    def _fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        seen["headers"] = headers
        return _FakeResponse(200, {"output": "done"})

    monkeypatch.setattr("app.services.tools.browser_use.safe_post", _fake_safe_post)
    run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")
    assert seen["headers"] == {}


def test_accepts_result_key_as_alternative_to_output(monkeypatch):
    monkeypatch.setattr("app.services.tools.browser_use.safe_post",
                        lambda *a, **k: _FakeResponse(200, {"result": "alt shape"}))
    result = run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")
    assert result["content"] == "alt shape"


def test_missing_url_in_response_falls_back_to_endpoint(monkeypatch):
    monkeypatch.setattr("app.services.tools.browser_use.safe_post",
                        lambda *a, **k: _FakeResponse(200, {"output": "done"}))
    result = run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")
    assert result["url"] == "https://browser-use.example.com/run"


def test_upstream_non_200_raises(monkeypatch):
    monkeypatch.setattr("app.services.tools.browser_use.safe_post",
                        lambda *a, **k: _FakeResponse(500, {}))
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")


def test_upstream_invalid_json_raises(monkeypatch):
    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr("app.services.tools.browser_use.safe_post", lambda *a, **k: _BadJson(200))
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")


def test_upstream_non_dict_json_raises(monkeypatch):
    monkeypatch.setattr("app.services.tools.browser_use.safe_post",
                        lambda *a, **k: _FakeResponse(200, ["not", "a", "dict"]))
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")


def test_ssrf_block_is_wrapped_as_browser_use_error(monkeypatch):
    from app.services.tools.ssrf_guard import SsrfBlockedError

    def _blocked(*a, **k):
        raise SsrfBlockedError("SSRF_BLOCKED_TARGET:x:10.0.0.1")

    monkeypatch.setattr("app.services.tools.browser_use.safe_post", _blocked)
    with pytest.raises(BrowserUseError):
        run_browser_task(endpoint="https://browser-use.example.com/run", api_key=None, task="x")
