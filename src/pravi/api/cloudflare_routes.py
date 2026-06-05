"""Cloudflare token-onboarding routes.

Cloudflare doesn't expose a self-serve third-party OAuth program, so the
"Connect Cloudflare" flow is paste-a-token-from-the-dashboard. This
module hides the friction: probe `/accounts` with the pasted token to
discover the account (single-account = auto-pick), persist into
`cloudflare_connections`, and the existing Pages helpers in
[[pravi.services.cloudflare]] start using the DB-stored creds.

Mounted under /api/auth/cloudflare/* by the FastAPI app.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from pravi.api.schemas import (
    CloudflareAccountOut,
    CloudflareConnectionOut,
    CloudflareConnectRequest,
)
from pravi.services import cloudflare as cf

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/auth/cloudflare")


@router.get("/me", response_model=CloudflareConnectionOut | None)
async def me() -> CloudflareConnectionOut | None:
    """Return the active Cloudflare connection, or null if not connected.
    UI uses this to render "connected as X" instead of the connect CTA."""
    conn = await cf.get_active_connection()
    if conn is None:
        return None
    return CloudflareConnectionOut(
        id=conn.id,
        account_id=conn.account_id,
        account_name=conn.account_name,
        token_id=conn.token_id,
        created_at=conn.created_at,
    )


@router.post("/connect", response_model=CloudflareConnectionOut)
async def connect(req: CloudflareConnectRequest) -> CloudflareConnectionOut:
    """Verify a pasted API token and persist it as the active connection.

    Flow:
      1. Probe `/user/tokens/verify` → reject 401/403 as "bad token".
      2. Probe `/accounts` → discover what the token can see.
      3. If exactly one account → auto-pick it.
      4. If multiple and `account_id` provided → use that one.
      5. If multiple and `account_id` not provided → 409 with the list
         so the UI can render a picker.
      6. Insert a fresh `cloudflare_connections` row.
    """
    token = (req.api_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="api_token is required")

    try:
        token_id, accounts = await cf.verify_token(token)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    picked: cf.CloudflareAccount
    if len(accounts) == 1:
        picked = accounts[0]
    elif req.account_id:
        match = next((a for a in accounts if a.id == req.account_id), None)
        if match is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"account_id {req.account_id!r} not in the list this "
                    f"token can see: {[a.id for a in accounts]}"
                ),
            )
        picked = match
    else:
        # Multiple accounts, no preference — surface the list to the UI
        # so the user can pick. 409 mirrors how GitHub's API signals
        # "more input needed" on otherwise-valid input.
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "account_picker_required",
                "message": "This token can access multiple accounts — pick one.",
                "accounts": [
                    CloudflareAccountOut(id=a.id, name=a.name).model_dump()
                    for a in accounts
                ],
            },
        )

    conn = await cf.store_connection(
        api_token=token,
        account_id=picked.id,
        account_name=picked.name,
        token_id=token_id,
    )
    return CloudflareConnectionOut(
        id=conn.id,
        account_id=conn.account_id,
        account_name=conn.account_name,
        token_id=conn.token_id,
        created_at=conn.created_at,
    )


@router.post("/disconnect", status_code=200)
async def disconnect() -> dict:
    """Soft-delete the active Cloudflare connection. Future Pages calls
    fall back to env-var creds if any, else `is_configured()` flips
    false and the create-repo modal disables the Pages toggle."""
    revoked = await cf.revoke_active_connection()
    return {"revoked": revoked}
