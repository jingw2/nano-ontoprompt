# backend/tests/agent/test_untrusted_artifact.py
"""P7A: UntrustedArtifact + Safe Markdown."""
from app.services.untrusted_artifact import make_artifact, safe_markdown


def test_strips_script_tags():
    out = safe_markdown("hello <script>alert(1)</script> world")
    assert "script" not in out.lower()
    assert "alert" not in out
    assert "hello" in out and "world" in out


def test_blocks_javascript_protocol_link():
    out = safe_markdown("[click me](javascript:alert(1))")
    assert "javascript:" not in out
    assert "blocked link" in out


def test_preserves_safe_link_and_exposes_destination():
    out = safe_markdown("[OntoPrompt](https://ontoprompt.example.com)")
    assert "OntoPrompt" in out
    assert "https://ontoprompt.example.com" in out


def test_strips_html_tags():
    out = safe_markdown("<div class='x'>plain <b>text</b></div>")
    assert "<div" not in out and "<b>" not in out
    assert "plain" in out and "text" in out


def test_make_artifact_hash_is_stable_for_identical_sanitized_content():
    a = make_artifact(source="https://x.example.com", media_type="text/html", raw_content="<p>hi</p>")
    b = make_artifact(source="https://y.example.com", media_type="text/html", raw_content="<p>hi</p>")
    assert a.hash == b.hash  # hash covers sanitized content, not source
    assert a.sanitized_content == "hi"


def test_make_artifact_default_sensitivity_is_low():
    artifact = make_artifact(source="s", media_type="text/plain", raw_content="hi")
    assert artifact.sensitivity == "low"
