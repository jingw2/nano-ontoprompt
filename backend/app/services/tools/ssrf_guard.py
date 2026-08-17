"""SSRF-safe HTTP fetch (P7A external tools, Section 8/10).

Resolves DNS and validates every hop's destination IP before connecting —
blocks private, loopback, link-local, multicast, reserved, and unspecified
ranges (this covers the cloud-metadata endpoint 169.254.169.254, which is
link-local) — and independently validates each redirect hop rather than
trusting httpx's automatic redirect follower. Streams the response body in
bounded chunks, aborting the read as soon as the byte cap is exceeded,
instead of trusting a possibly-absent or dishonest Content-Length header.

Known residual gap: this validates DNS *before* connecting but does not pin
the validated IP into the actual TCP connect, so a narrow DNS-rebinding
race (attacker's resolver returns a public IP for the validation lookup,
then a private IP for the real connect a few milliseconds later) is not
covered. Closing that gap needs a custom transport that connects directly
to the validated IP while still sending the correct TLS SNI/Host — out of
scope for this task; do not describe this guard as rebinding-safe.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_REDIRECTS = 5
BLOCKED_PORTS = frozenset({22, 25, 3306, 5432, 6379, 9200, 11211})


class SsrfBlockedError(Exception):
    """A fetch target resolved to a disallowed network destination."""


def _validate_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"DNS_RESOLUTION_FAILED:{host}") from exc
    if not infos:
        raise SsrfBlockedError(f"DNS_RESOLUTION_FAILED:{host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise SsrfBlockedError(f"SSRF_BLOCKED_TARGET:{host}:{ip}")


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlockedError(f"SSRF_BLOCKED_SCHEME:{parsed.scheme}")
    if not parsed.hostname:
        raise SsrfBlockedError("SSRF_BLOCKED_NO_HOST")
    if parsed.port and parsed.port in BLOCKED_PORTS:
        raise SsrfBlockedError(f"SSRF_BLOCKED_PORT:{parsed.port}")
    _validate_host(parsed.hostname)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return (parsed.scheme, parsed.hostname or "", parsed.port)


def safe_get(url: str, *, timeout_seconds: float, max_bytes: int,
            headers: dict | None = None) -> httpx.Response:
    current = url
    hop_headers = headers
    for _ in range(MAX_REDIRECTS + 1):
        _validate_url(current)
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
            with client.stream("GET", current, headers=hop_headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise SsrfBlockedError("SSRF_BLOCKED_REDIRECT_NO_LOCATION")
                    nxt = str(httpx.URL(current).join(location))
                    prev_origin = _origin(current)
                    next_origin = _origin(nxt)
                    # never forward credentials across an origin change — the SSRF
                    # guard validates network destinations, not credential trust.
                    # One-way latch: once dropped, headers stay dropped even if a
                    # later same-origin hop on the new host would re-attach them.
                    hop_headers = hop_headers if prev_origin == next_origin else None
                    current = nxt
                    continue
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise SsrfBlockedError(f"SSRF_BLOCKED_OVERSIZED_RESPONSE:{total}")
                    chunks.append(chunk)
                # Seed the fully-read body so the caller's .read()/.json() work
                # after the streaming context closes (httpx honors `_content`).
                response._content = b"".join(chunks)
                return response
    raise SsrfBlockedError("SSRF_BLOCKED_TOO_MANY_REDIRECTS")
