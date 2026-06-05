"""Cloudflare Pages helpers — minimum surface for the "create new repo +
auto-deploy to Pages" flow.

Auth: API token + account ID. Two sources, in priority order:

  1. The active row in `cloudflare_connections` (set via the in-app
     "Connect Cloudflare" modal). This is the preferred path — the user
     pastes a token, we probe `/accounts` to discover the account, and
     it persists across `pravi web` restarts.
  2. Env vars `PRAVI_CLOUDFLARE_API_TOKEN` + `PRAVI_CLOUDFLARE_ACCOUNT_ID`
     as a fallback for headless / CI setups.

The token needs `Account → Cloudflare Pages → Edit` permission for the
target account. The connect-modal's deep link to the token-create page
pre-templates this.

One-time external prerequisite the user MUST do via browser:
authorize Cloudflare's GitHub app on the GH account that owns the repo
(Cloudflare dashboard → Workers & Pages → Connect to Git). Without
this, the Pages-project-create call succeeds but the source binding
won't link — every subsequent push won't auto-deploy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from pravi.config import get_settings
from pravi.db.models import CloudflareConnection
from pravi.db.session import session_scope

log = structlog.get_logger(__name__)


_CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareNotConfigured(RuntimeError):
    """Raised when a Cloudflare call is attempted without an API token
    or account. The create-repo endpoint catches this and returns a
    structured 'pages: skipped — cloudflare not configured' result so
    the rest of the flow still succeeds."""


@dataclass
class PagesProjectInfo:
    """Trimmed-down Cloudflare Pages project payload — what the UI
    needs to render a 'live at X' chip and link out."""

    name: str
    subdomain: str  # e.g. "my-app.pages.dev"
    pages_url: str  # e.g. "https://my-app.pages.dev"
    canonical_url: str | None  # custom domain if any; usually None at create


@dataclass
class ActiveCloudflareConnection:
    """In-process snapshot of the active connection. Matches the shape
    of [[ActiveConnection]] in pravi.services.github."""

    id: int
    api_token: str
    account_id: str
    account_name: str | None
    token_id: str | None
    created_at: datetime


@dataclass
class CloudflareAccount:
    """One entry in the `/accounts` probe. The connect modal shows these
    when the token can see more than one account so the user picks."""

    id: str
    name: str


# ---- credential resolution ------------------------------------------------


async def _resolve_creds() -> tuple[str, str] | None:
    """Return (token, account_id) from the DB if available, else env, else
    None. Used by `is_configured()` and `_require_creds()`."""
    conn = await get_active_connection()
    if conn is not None:
        return conn.api_token, conn.account_id
    s = get_settings()
    if s.cloudflare_api_token and s.cloudflare_account_id:
        return s.cloudflare_api_token, s.cloudflare_account_id
    return None


async def _require_creds() -> tuple[str, str]:
    creds = await _resolve_creds()
    if creds is None:
        raise CloudflareNotConfigured(
            "Cloudflare is not configured. Click 'Connect Cloudflare' in "
            "the new-repo modal to set up a token, or set "
            "PRAVI_CLOUDFLARE_API_TOKEN + PRAVI_CLOUDFLARE_ACCOUNT_ID in .env."
        )
    return creds


async def is_configured() -> bool:
    """True when a Cloudflare token is reachable from DB or env. The UI
    uses this to gate the 'deploy to Pages' toggle on the create-repo
    modal."""
    return (await _resolve_creds()) is not None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---- token verification + connection persistence -------------------------


async def verify_token(
    token: str,
) -> tuple[str | None, list[CloudflareAccount]]:
    """Probe Cloudflare with a candidate token. Returns (token_id, accounts).

    Used by the connect modal:
      - 401 / 403 → raises RuntimeError; UI shows "token rejected".
      - Otherwise: token_id is the Cloudflare-assigned id (display hint
        in the UI for "which token is this") and accounts is the list
        of accounts this token can see (single → auto-pick; multiple →
        user picks).
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        # /user/tokens/verify is the canonical "is this token valid" probe.
        # It works for any token, no account binding required.
        rv = await client.get(
            f"{_CF_API_BASE}/user/tokens/verify",
            headers=_headers(token),
        )
        if rv.status_code in (401, 403):
            raise RuntimeError(
                "Cloudflare rejected this token. Double-check it was copied "
                "from the dashboard and has Account → Cloudflare Pages → Edit "
                "permission."
            )
        if rv.status_code >= 400:
            raise RuntimeError(
                f"cloudflare verify {rv.status_code}: {rv.text[:300]}"
            )
        verify_payload = rv.json().get("result") or {}
        token_id = verify_payload.get("id")

        # List accounts the token can see. With a token scoped to one
        # account this returns one row — we auto-pick it.
        ra = await client.get(
            f"{_CF_API_BASE}/accounts",
            headers=_headers(token),
            params={"per_page": 50},
        )
        if ra.status_code >= 400:
            raise RuntimeError(
                f"cloudflare list-accounts {ra.status_code}: {ra.text[:300]}"
            )
        accounts_payload = ra.json().get("result") or []

    accounts = [
        CloudflareAccount(id=a["id"], name=a.get("name") or a["id"])
        for a in accounts_payload
        if a.get("id")
    ]
    if not accounts:
        raise RuntimeError(
            "Token is valid but has no accounts attached. Recreate the token "
            "with 'All accounts' or pick a specific account in the dashboard."
        )
    return token_id, accounts


async def store_connection(
    *,
    api_token: str,
    account_id: str,
    account_name: str | None,
    token_id: str | None,
) -> ActiveCloudflareConnection:
    """Persist a connection row after a successful `verify_token` probe.
    The previous active connection (if any) is left in place but will
    be shadowed by `order_by(id desc)` — same pattern as GitHub."""
    async with session_scope() as session:
        row = CloudflareConnection(
            api_token=api_token,
            account_id=account_id,
            account_name=account_name,
            token_id=token_id,
        )
        session.add(row)
        await session.flush()
        log.info(
            "cloudflare.connected",
            connection_id=row.id,
            account_id=account_id,
            account_name=account_name,
        )
        return _to_active(row)


async def get_active_connection() -> ActiveCloudflareConnection | None:
    """Latest non-revoked connection, or None."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(CloudflareConnection)
                .where(CloudflareConnection.revoked_at.is_(None))
                .order_by(CloudflareConnection.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _to_active(row)


async def revoke_active_connection() -> bool:
    """Soft-delete the active connection. Returns True if a row was
    revoked. Same audit-trail pattern as the GitHub path."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(CloudflareConnection)
                .where(CloudflareConnection.revoked_at.is_(None))
                .order_by(CloudflareConnection.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        log.info("cloudflare.disconnected", connection_id=row.id)
        return True


def _to_active(row: CloudflareConnection) -> ActiveCloudflareConnection:
    return ActiveCloudflareConnection(
        id=row.id,
        api_token=row.api_token,
        account_id=row.account_id,
        account_name=row.account_name,
        token_id=row.token_id,
        created_at=row.created_at,
    )


# ---- Pages API helpers ----------------------------------------------------


async def pages_project_exists(name: str) -> bool:
    """Cheap availability check — Pages project names are unique per
    account and become subdomains. Used by the create-repo modal to
    warn before submit. 404 = available; 200 = taken."""
    token, account_id = await _require_creds()
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/{name}",
            headers=_headers(token),
        )
    if r.status_code == 404:
        return False
    if r.status_code == 200:
        return True
    log.warning(
        "cloudflare.pages_check_failed", status=r.status_code, body=r.text[:200]
    )
    return False


async def create_pages_project(
    *,
    name: str,
    github_owner: str,
    github_repo: str,
    production_branch: str,
    build_command: str,
    destination_dir: str,
    root_dir: str = "",
) -> PagesProjectInfo:
    """Create a Cloudflare Pages project bound to a GitHub repo.

    Once created, Cloudflare auto-deploys on every push to
    `production_branch`. The default `.pages.dev` subdomain is
    `{name}.pages.dev`.

    Assumes the Cloudflare → GitHub authorization is already in place
    on the user's account (see module docstring). Without it the create
    call may still succeed but the source binding silently won't fire
    builds.
    """
    token, account_id = await _require_creds()
    payload: dict[str, Any] = {
        "name": name,
        "production_branch": production_branch,
        "build_config": {
            "build_command": build_command,
            "destination_dir": destination_dir,
            "root_dir": root_dir,
        },
        "source": {
            "type": "github",
            "config": {
                "owner": github_owner,
                "repo_name": github_repo,
                "production_branch": production_branch,
                "pr_comments_enabled": True,
                "deployments_enabled": True,
                "production_deployment_enabled": True,
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects",
            headers=_headers(token),
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(
            f"cloudflare create_pages_project {r.status_code}: {r.text[:400]}"
        )
    result = r.json().get("result") or {}
    project_name = result.get("name", name)
    subdomain = result.get("subdomain") or f"{project_name}.pages.dev"
    canonical = result.get("canonical_deployment", {}).get("aliases") or []
    canonical_url = canonical[0] if canonical else None
    log.info(
        "cloudflare.pages_project_created",
        name=project_name,
        owner=github_owner,
        repo=github_repo,
    )
    return PagesProjectInfo(
        name=project_name,
        subdomain=subdomain,
        pages_url=f"https://{subdomain}",
        canonical_url=canonical_url,
    )
