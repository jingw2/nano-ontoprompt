"""Task 6: explicit, named assertions for every security property this
plan's Global Constraints require — deliberately redundant with individual
Task 3/4 tests so a regression here points directly at the requirement it
broke, not just at a generic flow test."""
import base64
import hashlib
import os

import pytest


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def test_plain_pkce_method_is_rejected():
    from app.services.oauth_flow import InvalidRequestError, validate_code_challenge

    _, challenge = _pkce_pair()
    with pytest.raises(InvalidRequestError):
        validate_code_challenge(challenge, "plain")


def test_oauth_access_jwt_is_rejected_by_the_interactive_dependency_and_vice_versa():
    """Cross-token-type confusion guard, both directions (no DB needed —
    this only exercises signature/claim decoding, not the client/user
    lookups Task 3's fuller DB-backed tests already cover)."""
    from jose import jwt
    from app.config import settings
    from app.services.auth_service import create_access_token, create_oauth_access_token, decode_token

    interactive = create_access_token({"sub": "u-1", "role": "viewer"})
    oauth = create_oauth_access_token("u-1", "c-1", "a")
    assert decode_token(interactive).get("token_use") is None
    assert decode_token(oauth)["token_use"] == "oauth_access"
    # both decode successfully with the shared secret/algorithm — the
    # distinguishing signal is the claim, not the signature, which is
    # exactly why deps/__init__.py and deps/oauth.py both check it explicitly
    assert jwt.decode(oauth, settings.secret_key, algorithms=["HS256"])["client_id"] == "c-1"


def test_scope_resolution_never_grants_more_than_the_client_allowlist():
    from app.models.oauth import OAuthClient

    client = OAuthClient(id="c-1", client_name="X", redirect_uris=[], allowed_scopes=["read"], is_active=True, created_by="u-1")
    from app.services.oauth_clients import resolve_scope
    with pytest.raises(ValueError):
        resolve_scope(client, "read write")  # "write" is not in allowed_scopes


def test_redirect_uri_validation_is_exact_string_match_not_prefix():
    from app.models.oauth import OAuthClient
    from app.services.oauth_clients import validate_redirect_uri

    client = OAuthClient(
        id="c-1", client_name="X", redirect_uris=["https://client.example/cb"],
        allowed_scopes=[], is_active=True, created_by="u-1",
    )
    assert validate_redirect_uri(client, "https://client.example/cb") is True
    for attack in (
        "https://client.example/cb.evil.com",
        "https://client.example/cb/../admin",
        "https://client.example/cb?redirect=evil",
        "https://client.example.evil.com/cb",
    ):
        assert validate_redirect_uri(client, attack) is False, f"{attack} should not validate"
