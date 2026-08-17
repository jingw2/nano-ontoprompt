# backend/app/services/untrusted_artifact.py
"""UntrustedArtifact + Safe Markdown (P7A external tools, Section 8).

Every external tool's input/output becomes an UntrustedArtifact before it
reaches the model or is stored: source, capture time, content hash, media
type, sensitivity, and sanitized content. Safe Markdown strips HTML tags,
script content, and unsafe URL protocols (javascript:, data:, vbscript:)
while preserving the visible link text and exposing the destination
alongside it, so a malicious page can influence what the model reads but
never execute anything or hide a link's real target.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_UNSAFE_PROTOCOLS = ("javascript:", "data:", "vbscript:", "file:")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_HTML_TAG = re.compile(r"<[^>]*>")
_SCRIPT_BLOCK = re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style\b.*?</style>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class UntrustedArtifact:
    source: str
    time: datetime
    hash: str
    media_type: str
    sensitivity: str
    sanitized_content: str


def safe_markdown(raw: str) -> str:
    """Strip HTML/scripts/unsafe URL protocols; keep link text and expose
    the destination as `text (-> url)` rather than a clickable/executable
    link."""
    text = _SCRIPT_BLOCK.sub("", raw)
    text = _STYLE_BLOCK.sub("", text)
    text = html.unescape(text)

    def _link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2).strip()
        if any(url.lower().startswith(proto) for proto in _UNSAFE_PROTOCOLS):
            return f"{label} (blocked link)"
        return f"{label} (-> {url})"

    text = _MARKDOWN_LINK.sub(_link, text)
    text = _HTML_TAG.sub("", text)
    return text.strip()


def make_artifact(*, source: str, media_type: str, raw_content: str, sensitivity: str = "low") -> UntrustedArtifact:
    sanitized = safe_markdown(raw_content)
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    return UntrustedArtifact(
        source=source, time=datetime.now(timezone.utc), hash=digest,
        media_type=media_type, sensitivity=sensitivity, sanitized_content=sanitized,
    )
