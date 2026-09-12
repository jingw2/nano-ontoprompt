"""Governed web Search adapter (P7A external tools, Section 8/10).

Calls a JSON search API (endpoint + optional credential from the bound
ToolConnectionVersion) through the SSRF guard, wraps every result's snippet
as an UntrustedArtifact. Never called directly by the model — only through
ToolGateway (see app/services/tool_gateway.py)."""
from __future__ import annotations

from urllib.parse import urlencode

from app.services.tools.ssrf_guard import SsrfBlockedError, safe_get, safe_post
from app.services.untrusted_artifact import make_artifact

# known real search-API request shapes a ToolConnectionVersion's
# `search_provider` may select (see migration 0047) — `generic`/None keeps
# the original Bearer-header GET behavior for a self-hosted proxy that
# already speaks this adapter's own response shape
SEARCH_PROVIDERS = ("generic", "bing", "google", "serper", "brave")


class SearchError(Exception):
    """A Search call failed or was rejected."""


def _build_request(provider: str, endpoint: str, api_key: str | None,
                   query: str, result_limit: int) -> tuple[str, str, dict | None, dict | None]:
    """Returns (method, url, headers, json_body) for one provider's actual
    request contract — these three differ from each other and from this
    adapter's original generic shape in auth placement (header vs query
    param), header name, and HTTP method, not just response format."""
    if provider == "bing":
        # Bing Web Search API v7: header auth, GET
        headers = {"Ocp-Apim-Subscription-Key": api_key} if api_key else None
        url = f"{endpoint}?{urlencode({'q': query, 'count': int(result_limit)})}"
        return "GET", url, headers, None
    if provider == "google":
        # Google Custom Search JSON API: key as a query param; the search
        # engine id (`cx`) has no home in this schema, so the admin folds it
        # into the endpoint URL itself (the frontend preset shows exactly
        # where): e.g. https://www.googleapis.com/customsearch/v1?cx=<id>
        separator = "&" if "?" in endpoint else "?"
        params: dict = {"q": query, "num": int(result_limit)}
        if api_key:
            params["key"] = api_key
        url = f"{endpoint}{separator}{urlencode(params)}"
        return "GET", url, None, None
    if provider == "serper":
        # Serper.dev: POST with a JSON body, header auth
        headers = {"X-API-KEY": api_key} if api_key else {}
        return "POST", endpoint, headers, {"q": query, "num": int(result_limit)}
    if provider == "brave":
        # Brave Search API: header auth, GET
        headers = {"X-Subscription-Token": api_key} if api_key else None
        url = f"{endpoint}?{urlencode({'q': query, 'count': int(result_limit)})}"
        return "GET", url, headers, None
    # generic (including None/unset, e.g. a version created before this
    # column existed): the adapter's original Bearer-header GET
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    url = f"{endpoint}?{urlencode({'q': query, 'count': int(result_limit)})}"
    return "GET", url, headers, None


def _extract_raw_results(body: dict) -> list[dict]:
    """Normalize a search response to `{title, url, snippet}` items.

    An admin who activates this connection is expected to point it at a real
    search API, and none of them share this adapter's own `{"results": [...]}`
    shape — every provider a deployment plausibly wires up here (a self-hosted
    proxy that already matches our shape, Google's Custom Search JSON API,
    Serper.dev, Bing's Web Search API v7, or the Brave Search API) has a
    different top-level key and different field names. Without normalizing
    them, a correctly-configured real endpoint silently returns zero results
    (the connection tests "healthy" — it got a 200 with valid JSON — but
    every Agent search comes back empty), which looks identical to search
    being broken. Checked in a
    fixed, documented order so a body that happens to satisfy more than one
    shape's key resolves deterministically."""
    if isinstance(body.get("results"), list):  # this adapter's own shape
        return [dict(item) for item in body["results"] if isinstance(item, dict)]
    if isinstance(body.get("items"), list):  # Google Custom Search JSON API
        return [{"title": item.get("title"), "url": item.get("link"), "snippet": item.get("snippet")}
                for item in body["items"] if isinstance(item, dict)]
    if isinstance(body.get("organic"), list):  # Serper.dev
        return [{"title": item.get("title"), "url": item.get("link"), "snippet": item.get("snippet")}
                for item in body["organic"] if isinstance(item, dict)]
    web_pages = body.get("webPages")
    if isinstance(web_pages, dict) and isinstance(web_pages.get("value"), list):  # Bing Web Search API v7
        return [{"title": item.get("name"), "url": item.get("url"), "snippet": item.get("snippet")}
                for item in web_pages["value"] if isinstance(item, dict)]
    web = body.get("web")
    if isinstance(web, dict) and isinstance(web.get("results"), list):  # Brave Search API
        return [{"title": item.get("title"), "url": item.get("url"), "snippet": item.get("description")}
                for item in web["results"] if isinstance(item, dict)]
    return []


def web_search(*, endpoint: str, api_key: str | None, query: str,
               result_limit: int = 5, timeout_seconds: float = 10.0,
               provider: str | None = None) -> list[dict]:
    if not endpoint:
        raise SearchError("SEARCH_ENDPOINT_MISSING")
    if not query.strip():
        raise SearchError("SEARCH_QUERY_EMPTY")
    method, url, headers, json_body = _build_request(
        provider or "generic", endpoint, api_key, query, result_limit)
    try:
        if method == "POST":
            response = safe_post(url, timeout_seconds=timeout_seconds, max_bytes=1_000_000,
                                 json_body=json_body, headers=headers)
        else:
            response = safe_get(url, timeout_seconds=timeout_seconds, max_bytes=1_000_000, headers=headers)
    except SsrfBlockedError as exc:
        raise SearchError(f"SEARCH_BLOCKED:{exc}") from exc
    if response.status_code != 200:
        raise SearchError(f"SEARCH_UPSTREAM_ERROR:{response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise SearchError("SEARCH_UPSTREAM_INVALID_JSON") from exc
    results = []
    for item in _extract_raw_results(body)[:result_limit]:
        title = str(item.get("title") or "")
        result_url = str(item.get("url") or "")
        snippet = str(item.get("snippet") or "")
        artifact = make_artifact(source=result_url or endpoint, media_type="text/plain", raw_content=snippet)
        results.append({"title": title, "url": result_url, "artifact": artifact})
    return results
