"""Security-header middleware factory (exported for I-BACKEND wiring).

Sets the fixed browser-hardening headers on every HTTP response: a strict
Content-Security-Policy without unsafe-inline/unsafe-eval, `no-referrer`,
`nosniff`, and a restrictive Permissions-Policy.
"""
from __future__ import annotations

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'; connect-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


def create_security_headers_middleware():
    """Return the ASGI middleware class to install via `app.add_middleware(...)`."""

    class SecurityHeadersMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    names = {name.lower() for name, _ in headers}
                    for name, value in SECURITY_HEADERS.items():
                        encoded_name = name.lower().encode()
                        if encoded_name not in names:
                            headers.append((encoded_name, value.encode()))
                    message["headers"] = headers
                await send(message)

            return await self.app(scope, receive, send_wrapper)

    return SecurityHeadersMiddleware
