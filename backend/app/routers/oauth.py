"""OAuth 2.0 + PKCE authorization server endpoints (P7E plan 1 of 2).

/authorize + /consent + /token + /revoke follow RFC 6749/7636/7009.
/token and /revoke are form-encoded and return spec-shaped (unwrapped) JSON
per this codebase's oauth-pkce-authorization-server plan's Global
Constraints — every other endpoint here uses the usual {"data": ...} shape.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_current_user, get_db, require_admin
from app.limiter import limiter
from app.models.user import User
from app.schemas.oauth import (
    OAuthClientCreateRequest, OAuthClientOut, OAuthClientPublicOut,
    OAuthConsentRequest, OAuthConsentResponse,
)
from app.services import oauth_clients, oauth_flow
from app.services.oauth_flow import OAuthFlowError

router = APIRouter()


def _append_query(uri: str, params: dict) -> str:
    parts = urlsplit(uri)
    merged = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(merged)))


def _spec_error(exc: OAuthFlowError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": exc.error, "error_description": exc.description})


@router.get("/oauth/authorize")
def authorize(
    response_type: str, client_id: str, redirect_uri: str,
    code_challenge: str, code_challenge_method: str,
    scope: str | None = None, state: str | None = None,
    db: Session = Depends(get_db),
):
    if response_type != "code":
        raise HTTPException(status_code=400, detail="unsupported response_type")
    client = oauth_clients.get_client(db, client_id)
    if client is None or not client.is_active:
        raise HTTPException(status_code=400, detail="unknown or inactive client_id")
    if not oauth_clients.validate_redirect_uri(client, redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri not registered for this client")
    try:
        oauth_flow.validate_code_challenge(code_challenge, code_challenge_method)
        oauth_clients.resolve_scope(client, scope)
    except (OAuthFlowError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(getattr(exc, "description", exc)))
    params = {"client_id": client_id, "redirect_uri": redirect_uri,
              "code_challenge": code_challenge, "code_challenge_method": code_challenge_method}
    if scope:
        params["scope"] = scope
    if state:
        params["state"] = state
    return RedirectResponse(_append_query(f"{settings.oauth_frontend_base_url}/oauth/consent", params), status_code=302)


@router.get("/oauth/clients/{client_id}")
def get_client_public(client_id: str, db: Session = Depends(get_db)):
    client = oauth_clients.get_client(db, client_id)
    if client is None or not client.is_active:
        raise HTTPException(status_code=404, detail="not found")
    return {"data": OAuthClientPublicOut(client_id=client.id, client_name=client.client_name).model_dump()}


@router.post("/oauth/consent")
def consent(
    body: OAuthConsentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = oauth_clients.get_client(db, body.client_id)
    if client is None or not client.is_active:
        raise HTTPException(status_code=400, detail="unknown or inactive client_id")
    if not oauth_clients.validate_redirect_uri(client, body.redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri not registered for this client")
    if body.decision == "deny":
        params = {"error": "access_denied"}
        if body.state:
            params["state"] = body.state
        return {"data": OAuthConsentResponse(redirect_uri=_append_query(body.redirect_uri, params)).model_dump()}
    if body.decision != "allow":
        raise HTTPException(status_code=400, detail="decision must be allow or deny")
    try:
        oauth_flow.validate_code_challenge(body.code_challenge, body.code_challenge_method)
        scope = oauth_clients.resolve_scope(client, body.scope)
    except (OAuthFlowError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(getattr(exc, "description", exc)))
    code = oauth_flow.issue_authorization_code(
        db, client_id=client.id, user_id=current_user.id, redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge, code_challenge_method=body.code_challenge_method, scope=scope,
    )
    params = {"code": code}
    if body.state:
        params["state"] = body.state
    return {"data": OAuthConsentResponse(redirect_uri=_append_query(body.redirect_uri, params)).model_dump()}


@router.post("/oauth/token")
@limiter.limit("20/minute")
def token(
    request: Request,
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str = Form(...),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        if grant_type == "authorization_code":
            if not (code and redirect_uri and code_verifier):
                raise oauth_flow.InvalidRequestError("code, redirect_uri, and code_verifier are required")
            access_token, new_refresh, scope, expires_in = oauth_flow.exchange_authorization_code(
                db, code=code, client_id=client_id, redirect_uri=redirect_uri, code_verifier=code_verifier,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise oauth_flow.InvalidRequestError("refresh_token is required")
            access_token, new_refresh, scope, expires_in = oauth_flow.rotate_oauth_refresh(
                db, refresh_token=refresh_token, client_id=client_id,
            )
        else:
            raise oauth_flow.OAuthFlowError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
    except OAuthFlowError as exc:
        return _spec_error(exc)
    return {
        "access_token": access_token, "token_type": "Bearer",
        "expires_in": expires_in, "refresh_token": new_refresh, "scope": scope,
    }


@router.post("/oauth/revoke")
@limiter.limit("20/minute")
def revoke(
    request: Request,
    token: str = Form(...),
    client_id: str = Form(...),
    db: Session = Depends(get_db),
):
    oauth_flow.revoke_oauth_refresh(db, refresh_token=token, client_id=client_id)
    return {}


@router.post("/oauth/clients", status_code=201)
def create_client(
    body: OAuthClientCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    client = oauth_clients.create_client(
        db, client_name=body.client_name, redirect_uris=body.redirect_uris,
        allowed_scopes=body.allowed_scopes, created_by=current_user.id,
    )
    return {"data": OAuthClientOut(
        id=client.id, client_name=client.client_name, redirect_uris=client.redirect_uris,
        allowed_scopes=client.allowed_scopes, is_active=client.is_active,
    ).model_dump()}


@router.get("/oauth/clients")
def list_clients_admin(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    return {"data": [
        OAuthClientOut(
            id=c.id, client_name=c.client_name, redirect_uris=c.redirect_uris,
            allowed_scopes=c.allowed_scopes, is_active=c.is_active,
        ).model_dump()
        for c in oauth_clients.list_clients(db)
    ]}
