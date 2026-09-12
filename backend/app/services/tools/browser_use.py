"""Browser Use adapter (P7A external tools).

Delegates a natural-language browsing task to an external Browser
Use-compatible HTTP service (self-hosted or third-party) and wraps its
result as an UntrustedArtifact. Unlike the Playwright adapter — which
renders one page itself, in-process, with a fixed byte/time cap — the
actual multi-step browsing session (clicking, filling forms, navigating
across pages) runs entirely on whatever service `endpoint` points at; this
adapter is a thin, SSRF-guarded client, not a browser. Never called
directly by the model — only through ToolGateway (see
app/services/tool_gateway.py)."""
from __future__ import annotations

from app.services.tools.ssrf_guard import SsrfBlockedError, safe_post
from app.services.untrusted_artifact import make_artifact


class BrowserUseError(Exception):
    """A Browser Use call failed or was rejected."""


def run_browser_task(*, endpoint: str, api_key: str | None, task: str,
                     timeout_seconds: float = 60.0) -> dict:
    if not endpoint:
        raise BrowserUseError("BROWSER_USE_ENDPOINT_MISSING")
    if not task.strip():
        raise BrowserUseError("BROWSER_USE_TASK_EMPTY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = safe_post(endpoint, timeout_seconds=timeout_seconds, max_bytes=1_000_000,
                             json_body={"task": task}, headers=headers)
    except SsrfBlockedError as exc:
        raise BrowserUseError(f"BROWSER_USE_BLOCKED:{exc}") from exc
    if response.status_code != 200:
        raise BrowserUseError(f"BROWSER_USE_UPSTREAM_ERROR:{response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise BrowserUseError("BROWSER_USE_UPSTREAM_INVALID_JSON") from exc
    if not isinstance(body, dict):
        raise BrowserUseError("BROWSER_USE_UPSTREAM_INVALID_JSON")
    # accept a few plausible key names rather than one fixed contract — the
    # admin may point this at their own self-hosted service, not just one
    # named vendor's exact response shape
    content = str(body.get("output") or body.get("result") or body.get("content") or body.get("text") or "")
    final_url = str(body.get("url") or body.get("final_url") or endpoint)
    artifact = make_artifact(source=final_url, media_type="text/plain", raw_content=content)
    return {"content": content, "url": final_url, "artifact": artifact}
