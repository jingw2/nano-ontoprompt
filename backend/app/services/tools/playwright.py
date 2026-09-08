"""Sandboxed Playwright adapter (P7B external tools, Section 8/10).

Drives a headless Chromium child process through Playwright's sync API to
fetch and render one web page for the model. In-code sandbox controls:

- every navigation AND subresource request is validated through the SSRF
  guard (`_validate_url`: private/loopback/link-local/multicast/reserved/
  unspecified IPs blocked, non-http(s) schemes blocked, disallowed ports
  blocked) and the connection version's domain allowlist (fail-closed);
- downloads are disabled (`accept_downloads=False` plus per-request
  validation) and popup pages are closed;
- extraction is byte-capped with a strict per-call time budget.

OS-level isolation (container/firejail) is deployment infrastructure and
remains a documented residual gap — the same honesty as the DNS-rebinding
note in ssrf_guard. This adapter must not be described as container-grade
sandboxed anywhere.
"""
from __future__ import annotations

from app.services.tools.ssrf_guard import SsrfBlockedError, _validate_url
from app.services.untrusted_artifact import make_artifact


class PlaywrightError(Exception):
    """A Playwright call failed or was rejected."""


def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    from urllib.parse import urlparse
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False
    for domain in allowed_domains:
        domain = domain.lower().strip().strip(".")
        if not domain:
            continue
        if hostname == domain or hostname.endswith("." + domain):
            return True
    return False


def browse_page(*, url: str, allowed_domains: list[str],
                timeout_seconds: float = 20.0, max_bytes: int = 200_000) -> dict:
    if not url.strip():
        raise PlaywrightError("PLAYWRIGHT_URL_BLOCKED:empty-url")
    try:
        _validate_url(url)
    except SsrfBlockedError as exc:
        raise PlaywrightError(f"PLAYWRIGHT_URL_BLOCKED:{exc}") from exc
    if not allowed_domains or not _domain_allowed(url, allowed_domains):
        raise PlaywrightError("PLAYWRIGHT_DOMAIN_NOT_ALLOWED")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightError("PLAYWRIGHT_UNAVAILABLE") from exc

    try:
        browser = None
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.set_default_timeout(timeout_seconds * 1000)

                def _route_handler(route) -> None:
                    request_url = route.request.url
                    try:
                        _validate_url(request_url)
                    except SsrfBlockedError:
                        route.abort()
                        return
                    if not _domain_allowed(request_url, allowed_domains):
                        route.abort()
                        return
                    route.continue_()

                page.route("**/*", _route_handler)
                context.on("page", lambda popup: popup.close())

                page.goto(url, wait_until="domcontentloaded")
                title = page.title() or ""
                final_url = page.url or url
                text = page.evaluate(
                    f"document.body ? document.body.innerText.slice(0, {int(max_bytes) + 1}) : ''",
                    timeout=timeout_seconds * 1000,
                ) or ""
            finally:
                if browser is not None:
                    browser.close()
    except PlaywrightError:
        raise
    except Exception as exc:
        name = type(exc).__name__
        if name in ("TimeoutError", "PlaywrightTimeoutError"):
            raise PlaywrightError("PLAYWRIGHT_TIMEOUT") from exc
        raise PlaywrightError(f"PLAYWRIGHT_NAVIGATION_FAILED:{name}:{str(exc)[:200]}") from exc

    if len(text.encode("utf-8")) > max_bytes:
        raise PlaywrightError(f"PLAYWRIGHT_RESPONSE_TOO_LARGE:{len(text.encode('utf-8'))}")

    artifact = make_artifact(source=final_url, media_type="text/plain",
                             raw_content=text, sensitivity="low")
    return {"title": title, "final_url": final_url, "artifact": artifact}
