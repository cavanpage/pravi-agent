"""GitHub OAuth + REST helpers.

Single-user local-dev model: the latest non-revoked row in
`github_connections` is "the" active connection. There's no per-user
session; whoever opens the browser gets to act as that account.

OAuth state is held in-memory (process-local). That's fine for one
uvicorn process; on restart the user just re-clicks "Connect GitHub".
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import select

from pravi.config import get_settings
from pravi.db.models import GitHubConnection
from pravi.db.session import session_scope

log = structlog.get_logger(__name__)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

# State store — maps state token → expiry epoch seconds. Cleaned on lookup.
_STATE_TTL_SECONDS = 600
_oauth_states: dict[str, float] = {}


class OAuthNotConfigured(RuntimeError):
    """Raised when the user tries to OAuth without client id/secret set."""


@dataclass
class ActiveConnection:
    """Snapshot of an active connection — what the UI displays + what the
    push/PR activity uses. Avoids leaking the SQLAlchemy row across session
    boundaries."""

    id: int
    access_token: str
    scopes: str | None
    github_user_id: int
    github_user_login: str
    github_user_avatar_url: str | None
    created_at: datetime


def _require_oauth_config() -> tuple[str, str]:
    s = get_settings()
    if not s.github_oauth_client_id or not s.github_oauth_client_secret:
        raise OAuthNotConfigured(
            "GitHub OAuth is not configured. Set PRAVI_GITHUB_OAUTH_CLIENT_ID + "
            "PRAVI_GITHUB_OAUTH_CLIENT_SECRET in .env and restart `pravi web`."
        )
    return s.github_oauth_client_id, s.github_oauth_client_secret


def build_authorize_url() -> str:
    """Return the GitHub authorize URL with a one-time state token."""
    client_id, _ = _require_oauth_config()
    settings = get_settings()
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.monotonic() + _STATE_TTL_SECONDS
    params = {
        "client_id": client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": settings.github_oauth_scopes.replace(",", " "),
        "state": state,
        # allow_signup default; force prompt so re-auth works cleanly.
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


def consume_state(state: str) -> bool:
    """Validate + remove a state token. Returns True if it was valid."""
    expiry = _oauth_states.pop(state, None)
    if expiry is None:
        return False
    if time.monotonic() > expiry:
        return False
    # Cheap GC of any other expired states while we're here.
    now = time.monotonic()
    for k in [k for k, exp in _oauth_states.items() if exp < now]:
        _oauth_states.pop(k, None)
    return True


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Hit /login/oauth/access_token and return the parsed JSON."""
    client_id, client_secret = _require_oauth_config()
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": settings.github_oauth_redirect_uri,
            },
        )
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(f"github oauth error: {data}")
    return data


async def fetch_github_user(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        r.raise_for_status()
        return r.json()


async def store_connection(token_payload: dict[str, Any]) -> ActiveConnection:
    """Persist a new connection row from a successful token exchange."""
    access_token = token_payload["access_token"]
    scopes = token_payload.get("scope") or None
    user = await fetch_github_user(access_token)

    async with session_scope() as session:
        row = GitHubConnection(
            access_token=access_token,
            scopes=scopes,
            github_user_id=int(user["id"]),
            github_user_login=str(user["login"]),
            github_user_avatar_url=user.get("avatar_url"),
        )
        session.add(row)
        await session.flush()
        log.info(
            "github.connected",
            connection_id=row.id,
            login=row.github_user_login,
        )
        return _to_active(row)


async def get_active_connection() -> ActiveConnection | None:
    """Latest non-revoked connection, or None."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(GitHubConnection)
                .where(GitHubConnection.revoked_at.is_(None))
                .order_by(GitHubConnection.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _to_active(row)


async def revoke_active_connection() -> bool:
    """Mark the active connection revoked. Returns True if one was revoked."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(GitHubConnection)
                .where(GitHubConnection.revoked_at.is_(None))
                .order_by(GitHubConnection.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        log.info("github.disconnected", connection_id=row.id)
        return True


def _to_active(row: GitHubConnection) -> ActiveConnection:
    return ActiveConnection(
        id=row.id,
        access_token=row.access_token,
        scopes=row.scopes,
        github_user_id=row.github_user_id,
        github_user_login=row.github_user_login,
        github_user_avatar_url=row.github_user_avatar_url,
        created_at=row.created_at,
    )


# ---- REST helpers ---------------------------------------------------------


async def search_user_repos(
    access_token: str, *, query: str = "", per_page: int = 25
) -> list[dict[str, Any]]:
    """Return repos accessible to the OAuth-authenticated user.

    For an empty query we list the user's own repos (push access) sorted by
    most-recently-pushed — the typical "show me my stuff" landing state.
    For a non-empty query we hit /search/repositories scoped to `user:<login>`
    so private repos the user owns are searchable too.

    Returns a normalized list — only the fields the UI / clone step needs.
    Errors propagate up; the caller turns httpx errors into a 4xx/5xx.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        if not query.strip():
            r = await client.get(
                "https://api.github.com/user/repos",
                headers=headers,
                params={
                    "sort": "pushed",
                    "direction": "desc",
                    "per_page": per_page,
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            r.raise_for_status()
            items = r.json()
        else:
            # Scope the search to the logged-in user so private repos appear.
            user = await fetch_github_user(access_token)
            qs = f"{query.strip()} user:{user['login']} fork:true"
            r = await client.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params={"q": qs, "per_page": per_page, "sort": "updated"},
            )
            r.raise_for_status()
            items = r.json().get("items", [])

    return [_normalize_repo(item) for item in items]


def _normalize_repo(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") or {}
    return {
        "owner": owner.get("login"),
        "name": item.get("name"),
        "full_name": item.get("full_name"),
        "description": item.get("description"),
        "private": bool(item.get("private")),
        "default_branch": item.get("default_branch") or "main",
        "clone_url": item.get("clone_url"),
        "ssh_url": item.get("ssh_url"),
        "updated_at": item.get("updated_at"),
    }


async def ensure_repo_cloned(
    *,
    owner: str,
    name: str,
    clone_url: str,
    access_token: str,
    base_dir: Path,
) -> Path:
    """Clone the GitHub repo into `base_dir/<owner>__<name>` if missing.

    Idempotent — if the directory already looks like a git checkout we leave
    it alone. The token is injected into the URL so private repos work; the
    saved remote keeps the bare HTTPS form (no token at rest) so it doesn't
    survive a `git remote -v` leak.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    target = base_dir / f"{owner}__{name}"
    if (target / ".git").is_dir():
        return target

    token_url = clone_url.replace(
        "https://", f"https://x-access-token:{access_token}@", 1
    )
    log.info("github.clone.start", owner=owner, name=name, target=str(target))
    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        token_url,
        str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Don't echo the token in errors.
        msg = stderr.decode(errors="replace").replace(access_token, "<redacted>")
        raise RuntimeError(f"git clone failed ({proc.returncode}): {msg[:500]}")

    # Rewrite the remote so the token doesn't sit on disk.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(target),
        "remote",
        "set-url",
        "origin",
        clone_url,
    )
    await proc.communicate()
    log.info("github.clone.done", owner=owner, name=name, target=str(target))
    return target


async def create_pull_request(
    access_token: str,
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = True,
) -> dict[str, Any]:
    """Create a PR. Returns the PR JSON (number + html_url etc.)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
        "draft": draft,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(
            f"github PR create {r.status_code}: {r.text[:500]}"
        )
    return r.json()
