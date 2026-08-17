"""Governed web Search adapter (P7A external tools, Section 8/10).

Calls a JSON search API (endpoint + optional bearer credential from the
bound ToolConnectionVersion) through the SSRF guard, wraps every result's
snippet as an UntrustedArtifact. Never called directly by the model — only
through ToolGateway (see app/services/tool_gateway.py)."""
from __future__ import annotations

from app.services.tools.ssrf_guard import SsrfBlockedError, safe_get
from app.services.untrusted_artifact import make_artifact


class SearchError(Exception):
    """A Search call failed or was rejected."""


def web_search(*, endpoint: str, api_key: str | None, query: str,
               result_limit: int = 5, timeout_seconds: float = 10.0) -> list[dict]:
    if not endpoint:
        raise SearchError("SEARCH_ENDPOINT_MISSING")
    if not query.strip():
        raise SearchError("SEARCH_QUERY_EMPTY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    url = f"{endpoint}?q={query}&count={int(result_limit)}"
    try:
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
    for item in (body.get("results") or [])[:result_limit]:
        title = str(item.get("title") or "")
        result_url = str(item.get("url") or "")
        snippet = str(item.get("snippet") or "")
        artifact = make_artifact(source=result_url or endpoint, media_type="text/plain", raw_content=snippet)
        results.append({"title": title, "url": result_url, "artifact": artifact})
    return results
