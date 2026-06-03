"""Temporal activity: push the dev branch + open a draft PR.

Runs after `dev_activity`. Consumes a `SandboxHandle` instead of raw
filesystem paths — git operations are delegated to the configured
Sandbox impl so a remote backend can push from inside itself without
the workflow caring.

If the dev agent didn't commit anything we short-circuit and don't push
(no point opening an empty PR). If the GitHub OAuth connection is
missing we log and skip — the dev step still counts as successful so the
user can connect GitHub later and re-push manually.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import structlog
from temporalio import activity

from pravi.agents.sandbox.factory import get_sandbox
from pravi.agents.sandbox.protocols import SandboxHandle
from pravi.db.models import Ticket
from pravi.db.session import session_scope
from pravi.services import github as gh

log = structlog.get_logger(__name__)


@dataclass
class PushAndOpenPRRequest:
    ticket_id: int
    ticket_external_id: str
    ticket_title: str
    handle: SandboxHandle
    base_ref: str  # PR base branch
    pr_body: str  # markdown — typically the approved plan + ticket body


@dataclass
class PushAndOpenPRResult:
    pushed: bool
    pr_number: int | None
    pr_url: str | None
    commits_pushed: int
    # Human-readable explanation when pushed=False or pr_number is None.
    skipped_reason: str | None = None
    error: str | None = None


# --- url parsing -----------------------------------------------------------

_HTTPS_RE = re.compile(
    r"^https?://(?:[^@]+@)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SSH_RE = re.compile(
    r"^(?:git@|ssh://(?:git@)?)github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    re.IGNORECASE,
)


def _parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from either HTTPS or SSH GitHub URL. None if not GitHub."""
    for rx in (_HTTPS_RE, _SSH_RE):
        m = rx.match(remote_url.strip())
        if m:
            return m.group("owner"), m.group("repo")
    return None


async def _persist_pr(ticket_id: int, *, pr_number: int, owner: str, repo: str) -> None:
    """Write pr_number on the Ticket row.

    We don't store a separate URL column — TicketOut composes it from
    repo.github_owner / github_name + pr_number. We do persist github_owner
    + github_name on the Repo row here so the UI can render the URL even
    without re-reading the remote.
    """
    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            return
        ticket.pr_number = pr_number
        repo_row = await session.get(type(ticket).repo.property.mapper.class_, ticket.repo_id)  # type: ignore[arg-type]
        if repo_row is not None:
            repo_row.github_owner = owner
            repo_row.github_name = repo
        log.info(
            "ticket.pr_attached",
            ticket_id=ticket_id,
            pr_number=pr_number,
            owner=owner,
            repo=repo,
        )


@activity.defn
async def push_and_open_pr(req: PushAndOpenPRRequest) -> PushAndOpenPRResult:
    sandbox = get_sandbox()
    handle = req.handle

    # 1) Did the dev agent commit anything? If not, nothing to push.
    n_commits = await sandbox.commits_ahead(handle, req.base_ref)
    if n_commits == 0:
        log.info("pr.skipped.no_commits", ticket=req.ticket_external_id)
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=0,
            skipped_reason=(
                "dev agent didn't commit anything — no PR to open. "
                "Check the sandbox and commit manually if you want a PR."
            ),
        )

    # 2) Resolve owner/name from the sandbox's origin URL.
    origin = handle.origin_url
    if origin is None:
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            error="repo has no `origin` remote configured",
        )
    parsed = _parse_github_remote(origin)
    if parsed is None:
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            skipped_reason=f"origin is not a GitHub remote ({origin!r}) — skipping PR",
        )
    owner, repo_name = parsed

    # 3) Need an OAuth connection to push + open the PR.
    conn = await gh.get_active_connection()
    if conn is None:
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            skipped_reason=(
                "no GitHub connection. Click 'Connect GitHub' in the web UI, "
                "then re-run this task."
            ),
        )

    # 4) Push the branch via the sandbox (lets remote backends push from
    #    inside themselves without filesystem assumptions on the worker).
    ok, msg = await sandbox.push_branch(
        handle, token=conn.access_token, owner=owner, name=repo_name
    )
    if not ok:
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            error=f"git push failed: {msg}",
        )
    log.info(
        "pr.pushed",
        ticket=req.ticket_external_id,
        branch=handle.branch,
        owner=owner,
        repo=repo_name,
        commits=n_commits,
    )

    # 5) Open the draft PR.
    try:
        pr = await gh.create_pull_request(
            conn.access_token,
            owner=owner,
            repo=repo_name,
            head=handle.branch,
            base=req.base_ref,
            title=req.ticket_title,
            body=req.pr_body,
            draft=True,
        )
    except Exception as e:
        return PushAndOpenPRResult(
            pushed=True,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            error=f"branch pushed but PR open failed: {type(e).__name__}: {e}",
        )

    pr_number = int(pr["number"])
    pr_url = pr.get("html_url") or f"https://github.com/{owner}/{repo_name}/pull/{pr_number}"
    await _persist_pr(req.ticket_id, pr_number=pr_number, owner=owner, repo=repo_name)
    log.info(
        "pr.opened",
        ticket=req.ticket_external_id,
        pr_number=pr_number,
        url=pr_url,
    )
    return PushAndOpenPRResult(
        pushed=True,
        pr_number=pr_number,
        pr_url=pr_url,
        commits_pushed=n_commits,
    )
