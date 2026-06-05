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

from pravi.api.schemas import (
    CreateRepoRequest,
    CreateRepoResult,
    GitHubConnectionOut,
    GitHubIssueOut,
    GitHubRepoOut,
    PagesProjectOut,
)
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


@router.get("/integrations")
async def integration_status() -> dict[str, dict[str, bool]]:
    """Which optional integrations are configured. The new-repo modal
    uses this to gate the 'deploy to Cloudflare Pages' toggle so the
    user doesn't tick something that'll silently no-op at submit time."""
    from pravi.services import cloudflare as cf

    return {
        "cloudflare": {"configured": await cf.is_configured()},
        "github": {"connected": (await gh.get_active_connection()) is not None},
    }


@router.post("/repos/new", response_model=CreateRepoResult)
async def create_new_repo(req: CreateRepoRequest) -> CreateRepoResult:
    """Create a brand-new GitHub repo, seed it with a starter template,
    optionally wire it up to Cloudflare Pages for auto-deploy, and
    register it as a pravi Repo so it's immediately usable as a ticket
    target.

    Steps:
      1. POST /user/repos → empty repo on GitHub
      2. Local temp checkout, write template files, push initial commit
      3. (Optional) Cloudflare Pages project tied to this repo
      4. (Optional) Register a pravi Repo row pointing at the local clone
    """
    from pathlib import Path as _Path

    from pravi.config import get_settings as _get_settings
    from pravi.db.models import Repo as _Repo
    from pravi.db.session import session_scope as _session_scope
    from pravi.services import cloudflare as cf
    from pravi.templates import ALL_TEMPLATES
    from pravi.templates.vite_react_static import render as _render_vite

    conn = await gh.get_active_connection()
    if conn is None:
        raise HTTPException(
            status_code=401,
            detail="not connected to GitHub — click 'Connect GitHub' first",
        )

    if req.template not in ALL_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown template {req.template!r}. Available: "
                f"{sorted(ALL_TEMPLATES.keys())}"
            ),
        )

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="repo name is required")

    # 1) Create the empty repo.
    try:
        repo_payload = await gh.create_repo(
            conn.access_token,
            name=name,
            description=req.description,
            private=req.private,
            auto_init=False,
        )
    except RuntimeError as e:
        msg = str(e)
        if "conflict" in msg.lower() or "422" in msg:
            raise HTTPException(
                status_code=409,
                detail=f"repo name {name!r} is taken on your account",
            ) from e
        raise HTTPException(status_code=502, detail=msg) from e

    owner = (repo_payload.get("owner") or {}).get("login") or conn.github_user_login
    default_branch = repo_payload.get("default_branch") or "main"
    repo_full_name = repo_payload.get("full_name") or f"{owner}/{name}"

    # 2) Push the initial commit with the template files.
    if req.template == "vite-react-static":
        files = _render_vite(project_name=name, repo_full_name=repo_full_name)
    else:
        files = ALL_TEMPLATES[req.template]

    commit_pushed = False
    try:
        await gh.push_initial_commit(
            conn.access_token,
            owner=owner,
            name=name,
            default_branch=default_branch,
            files=files,
        )
        commit_pushed = True
    except RuntimeError as e:
        # Repo was created but the commit failed. Return partial success
        # so the user can either delete the empty repo + retry or push
        # manually.
        log.warning(
            "github.create_repo.commit_failed",
            owner=owner,
            name=name,
            error=str(e),
        )

    # 3) Cloudflare Pages (best-effort).
    pages_out: PagesProjectOut | None = None
    pages_skipped: str | None = None
    if req.deploy_to_cloudflare_pages:
        if not await cf.is_configured():
            pages_skipped = (
                "Cloudflare not configured. Click 'Connect Cloudflare' in "
                "the new-repo modal, or set PRAVI_CLOUDFLARE_API_TOKEN + "
                "PRAVI_CLOUDFLARE_ACCOUNT_ID in .env."
            )
        elif not commit_pushed:
            pages_skipped = "initial commit didn't land — skipped Pages project"
        else:
            try:
                pages_info = await cf.create_pages_project(
                    name=name,
                    github_owner=owner,
                    github_repo=name,
                    production_branch=default_branch,
                    # Vite/React template's build settings — when more
                    # templates land we'll map this from the template.
                    build_command="npm run build",
                    destination_dir="dist",
                )
                pages_out = PagesProjectOut(
                    name=pages_info.name,
                    subdomain=pages_info.subdomain,
                    pages_url=pages_info.pages_url,
                    canonical_url=pages_info.canonical_url,
                )
            except Exception as e:
                log.warning(
                    "cloudflare.pages_create_failed",
                    name=name,
                    error=str(e),
                )
                pages_skipped = f"Pages create failed: {type(e).__name__}: {e}"

    # 4) Register a pravi Repo row so the user can immediately start
    #    epics against it. Lazy-clone is handled by the sandbox the
    #    first time a dev run fires.
    pravi_repo_id: int | None = None
    if req.register_in_pravi and commit_pushed:
        try:
            clone_url = repo_payload.get("clone_url") or (
                f"https://github.com/{owner}/{name}.git"
            )
            settings = _get_settings()
            target = await gh.ensure_repo_cloned(
                owner=owner,
                name=name,
                clone_url=clone_url,
                access_token=conn.access_token,
                base_dir=settings.clone_base_resolved,
            )
            async with _session_scope() as session:
                row = _Repo(
                    name=name,
                    local_path=str(_Path(target).resolve()),
                    github_owner=owner,
                    github_name=name,
                )
                session.add(row)
                await session.flush()
                pravi_repo_id = row.id
        except Exception as e:
            log.warning(
                "create_repo.pravi_register_failed",
                owner=owner,
                name=name,
                error=str(e),
            )

    # Build the normalized GitHubRepoOut. We reach into the private
    # `_normalize_repo` because GitHub's create-repo payload has the
    # same shape as the search/list endpoints — same normalizer applies.
    return CreateRepoResult(
        repo=GitHubRepoOut(**gh._normalize_repo(repo_payload)),
        initial_commit_pushed=commit_pushed,
        pages=pages_out,
        pages_skipped_reason=pages_skipped,
        pravi_repo_id=pravi_repo_id,
    )


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
