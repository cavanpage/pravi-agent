"""GitHub OAuth routes — "Connect GitHub" button + callback.

Mounted under /api/auth/github/* by the FastAPI app. The flow:

  1. UI hits  GET /api/auth/github/login   → 302 to GitHub's authorize page
  2. GitHub posts back to  GET /callback?code=…&state=…
  3. Server exchanges code → token, persists, redirects user to the home page
  4. UI calls GET /me to render the connected user; POST /logout to revoke
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from pravi.api.schemas import GitHubConnectionOut, GitHubIssueOut, GitHubRepoOut
from pravi.config import get_settings
from pravi.services import github as gh

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/auth/github")


@router.get("/login")
async def login() -> RedirectResponse:
    """Kick the OAuth flow. Browser-driven (returns a 302).

    The UI hits this via `window.location.href = "/api/auth/github/login"`
    rather than fetch() — we want the browser to follow the redirect.
    """
    try:
        url = gh.build_authorize_url()
    except gh.OAuthNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    """Token-exchange endpoint. GitHub redirects here with `code` + `state`.

    On success we redirect back to the configured `github_oauth_success_redirect`
    so the user lands on the app, not a JSON blob. Errors land on the same
    URL with `?github_auth_error=...` so the UI can surface a toast.
    """
    settings = get_settings()
    success = settings.github_oauth_success_redirect

    err = request.query_params.get("error")
    if err:
        return RedirectResponse(
            url=f"{success}?github_auth_error={err}", status_code=302
        )
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return RedirectResponse(
            url=f"{success}?github_auth_error=missing_code_or_state",
            status_code=302,
        )
    if not gh.consume_state(state):
        return RedirectResponse(
            url=f"{success}?github_auth_error=invalid_state",
            status_code=302,
        )
    try:
        token = await gh.exchange_code_for_token(code)
        await gh.store_connection(token)
    except Exception as e:
        log.exception("github.oauth_callback_failed", error=str(e))
        return RedirectResponse(
            url=f"{success}?github_auth_error={type(e).__name__}", status_code=302
        )
    return RedirectResponse(url=success, status_code=302)


@router.get("/me", response_model=GitHubConnectionOut | None)
async def me() -> GitHubConnectionOut | None:
    """Return the active GitHub connection, or null if not connected."""
    conn = await gh.get_active_connection()
    if conn is None:
        return None
    return GitHubConnectionOut(
        id=conn.id,
        github_user_login=conn.github_user_login,
        github_user_avatar_url=conn.github_user_avatar_url,
        scopes=conn.scopes,
        created_at=conn.created_at,
    )


@router.post("/logout", status_code=200)
async def logout() -> dict:
    """Revoke the active connection (soft-delete; row stays for audit)."""
    revoked = await gh.revoke_active_connection()
    return {"revoked": revoked}


@router.get(
    "/repos/{owner}/{name}/issues", response_model=list[GitHubIssueOut]
)
async def list_repo_issues(
    owner: str, name: str, state: str = "open", labels: str = ""
) -> list[GitHubIssueOut]:
    """List issues on a connected GitHub repo. PRs are filtered out.

    Used by the /issues page to scan + import as pravi tickets. `state` is
    "open" | "closed" | "all"; `labels` is comma-separated.
    """
    conn = await gh.get_active_connection()
    if conn is None:
        raise HTTPException(
            status_code=401,
            detail="not connected to GitHub — click 'Connect GitHub' first",
        )
    try:
        items = await gh.list_repo_issues(
            conn.access_token,
            owner=owner,
            name=name,
            state=state,
            labels=labels,
        )
    except Exception as e:
        log.exception(
            "github.list_issues_failed", owner=owner, name=name, error=str(e)
        )
        raise HTTPException(status_code=502, detail=f"GitHub issues: {e}") from e
    return [GitHubIssueOut(**it) for it in items]


@router.get("/repos/search", response_model=list[GitHubRepoOut])
async def search_repos(q: str = "") -> list[GitHubRepoOut]:
    """Search GitHub repos accessible to the active OAuth connection.

    Used by the new-ticket form to let the user pick a repo without typing
    a local path. Empty `q` returns the most-recently-pushed repos.
    """
    conn = await gh.get_active_connection()
    if conn is None:
        raise HTTPException(
            status_code=401,
            detail="not connected to GitHub — click 'Connect GitHub' first",
        )
    try:
        items = await gh.search_user_repos(conn.access_token, query=q)
    except Exception as e:
        log.exception("github.repos_search_failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"GitHub search failed: {e}") from e
    return [GitHubRepoOut(**item) for item in items if item.get("clone_url")]
