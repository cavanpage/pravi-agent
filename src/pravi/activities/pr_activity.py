"""Temporal activity: push the dev branch + open a PR (ready-for-review
by default; opt into draft via `PRAVI_PR_OPEN_AS_DRAFT=true`).

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
from pravi.config import get_settings
from pravi.db.models import Ticket
from pravi.db.session import session_scope
from pravi.services import github as gh

log = structlog.get_logger(__name__)


@dataclass
class PushBranchRequest:
    ticket_external_id: str
    handle: SandboxHandle
    base_ref: str


@dataclass
class PushBranchResult:
    pushed: bool
    commits_ahead: int
    # Full SHA of the tip we just pushed. The preview leg matches
    # Cloudflare deployments on this, so a build is unambiguously the
    # commit under test (ADR 0007).
    head_sha: str | None = None
    owner: str | None = None
    repo: str | None = None
    skipped_reason: str | None = None
    error: str | None = None


@dataclass
class OpenPRRequest:
    ticket_id: int
    ticket_title: str
    owner: str
    repo: str
    head_branch: str
    base_ref: str
    pr_body: str
    # None defers to settings.pr_open_as_draft. The e2e path passes True
    # so a PR that hasn't gone green yet doesn't read as ready.
    draft: bool | None = None


@dataclass
class OpenPRResult:
    pr_number: int | None
    pr_url: str | None
    error: str | None = None
    # True when an open PR for this head already existed and was reused.
    already_open: bool = False


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


async def _resolve_and_push(
    *, handle: SandboxHandle, base_ref: str, ticket_external_id: str
) -> PushBranchResult:
    """Steps 1-4 of shipping a branch: commits check → resolve owner/repo →
    GitHub connection → push → read back the pushed SHA.

    Shared by `push_branch` (which the loop calls once per iteration) and
    `push_and_open_pr` (the pre-0007 one-shot path), so both produce the
    same skip reasons and error strings.
    """
    sandbox = get_sandbox()

    # 1) Did the dev agent commit anything? If not, nothing to push.
    n_commits = await sandbox.commits_ahead(handle, base_ref)
    if n_commits == 0:
        log.info("pr.skipped.no_commits", ticket=ticket_external_id)
        return PushBranchResult(
            pushed=False,
            commits_ahead=0,
            skipped_reason=(
                "dev agent didn't commit anything — no PR to open. "
                "Check the sandbox and commit manually if you want a PR."
            ),
        )

    # 2) Resolve owner/name from the sandbox's origin URL.
    origin = handle.origin_url
    if origin is None:
        return PushBranchResult(
            pushed=False,
            commits_ahead=n_commits,
            error="repo has no `origin` remote configured",
        )
    parsed = _parse_github_remote(origin)
    if parsed is None:
        return PushBranchResult(
            pushed=False,
            commits_ahead=n_commits,
            skipped_reason=f"origin is not a GitHub remote ({origin!r}) — skipping PR",
        )
    owner, repo_name = parsed

    # 3) Need an OAuth connection to push + open the PR.
    conn = await gh.get_active_connection()
    if conn is None:
        return PushBranchResult(
            pushed=False,
            commits_ahead=n_commits,
            owner=owner,
            repo=repo_name,
            skipped_reason=(
                "no GitHub connection. Click 'Connect GitHub' in the web UI, then re-run this task."
            ),
        )

    # 4) Push the branch via the sandbox (lets remote backends push from
    #    inside themselves without filesystem assumptions on the worker).
    ok, msg = await sandbox.push_branch(
        handle, token=conn.access_token, owner=owner, name=repo_name
    )
    if not ok:
        return PushBranchResult(
            pushed=False,
            commits_ahead=n_commits,
            owner=owner,
            repo=repo_name,
            error=f"git push failed: {msg}",
        )

    head_sha = await sandbox.head_sha(handle)
    log.info(
        "pr.pushed",
        ticket=ticket_external_id,
        branch=handle.branch,
        owner=owner,
        repo=repo_name,
        commits=n_commits,
        head_sha=head_sha,
    )
    return PushBranchResult(
        pushed=True,
        commits_ahead=n_commits,
        head_sha=head_sha,
        owner=owner,
        repo=repo_name,
    )


@activity.defn
async def push_branch(req: PushBranchRequest) -> PushBranchResult:
    """Push the ticket's branch. Safe to retry — a re-push of the same
    branch is a fast-forward no-op."""
    return await _resolve_and_push(
        handle=req.handle,
        base_ref=req.base_ref,
        ticket_external_id=req.ticket_external_id,
    )


@activity.defn
async def open_pr(req: OpenPRRequest) -> OpenPRResult:
    """Open (or adopt) the PR for an already-pushed branch.

    Idempotent on purpose: with retries enabled, a partial failure would
    otherwise 422 on "A pull request already exists for this head". We
    look for an open PR on the head first and reuse it.
    """
    conn = await gh.get_active_connection()
    if conn is None:
        return OpenPRResult(
            pr_number=None,
            pr_url=None,
            error="no GitHub connection — cannot open a PR",
        )

    existing = await gh.find_open_pull_request(
        conn.access_token, owner=req.owner, repo=req.repo, head_branch=req.head_branch
    )
    if existing is not None:
        pr_number = int(existing["number"])
        pr_url = existing.get("html_url") or _pr_url(req.owner, req.repo, pr_number)
        await _persist_pr(req.ticket_id, pr_number=pr_number, owner=req.owner, repo=req.repo)
        log.info("pr.reused", pr_number=pr_number, url=pr_url)
        return OpenPRResult(pr_number=pr_number, pr_url=pr_url, already_open=True)

    draft = get_settings().pr_open_as_draft if req.draft is None else req.draft
    try:
        pr = await gh.create_pull_request(
            conn.access_token,
            owner=req.owner,
            repo=req.repo,
            head=req.head_branch,
            base=req.base_ref,
            title=req.ticket_title,
            body=req.pr_body,
            draft=draft,
        )
    except Exception as e:
        return OpenPRResult(
            pr_number=None,
            pr_url=None,
            error=f"PR open failed: {type(e).__name__}: {e}",
        )

    pr_number = int(pr["number"])
    pr_url = pr.get("html_url") or _pr_url(req.owner, req.repo, pr_number)
    await _persist_pr(req.ticket_id, pr_number=pr_number, owner=req.owner, repo=req.repo)
    log.info("pr.opened", pr_number=pr_number, url=pr_url, draft=draft)
    return OpenPRResult(pr_number=pr_number, pr_url=pr_url)


def _pr_url(owner: str, repo: str, number: int) -> str:
    return f"https://github.com/{owner}/{repo}/pull/{number}"


@activity.defn
async def push_and_open_pr(req: PushAndOpenPRRequest) -> PushAndOpenPRResult:
    """DEPRECATED — the one-shot push+PR path used before ADR 0007.

    `FeatureWorkflow` now calls `push_branch` + `open_pr` separately so
    the repair loop can push repeatedly against a single PR. Kept
    registered because in-flight workflow histories reference it.
    """
    handle = req.handle
    pushed = await _resolve_and_push(
        handle=handle, base_ref=req.base_ref, ticket_external_id=req.ticket_external_id
    )
    if not pushed.pushed:
        return PushAndOpenPRResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            commits_pushed=pushed.commits_ahead,
            skipped_reason=pushed.skipped_reason,
            error=pushed.error,
        )
    owner, repo_name = pushed.owner, pushed.repo
    n_commits = pushed.commits_ahead
    assert owner is not None and repo_name is not None

    conn = await gh.get_active_connection()
    if conn is None:
        return PushAndOpenPRResult(
            pushed=True,
            pr_number=None,
            pr_url=None,
            commits_pushed=n_commits,
            skipped_reason="no GitHub connection",
        )

    # 5) Open the PR. Defaults to "ready for review" — pravi's review
    #    gate is at PR-merge time, so a PR sitting in draft would just
    #    add an extra "promote to ready" click. Override via
    #    `PRAVI_PR_OPEN_AS_DRAFT=true` if you want them opened as draft.
    settings = get_settings()
    try:
        pr = await gh.create_pull_request(
            conn.access_token,
            owner=owner,
            repo=repo_name,
            head=handle.branch,
            base=req.base_ref,
            title=req.ticket_title,
            body=req.pr_body,
            draft=settings.pr_open_as_draft,
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


@dataclass
class CheckPRStateRequest:
    owner: str
    repo: str
    pr_number: int


@dataclass
class CheckPRStateResult:
    # "merged" | "closed" | "open" | "unknown" (no connection / API error —
    # the poll loop treats unknown as "still open" and tries again later).
    state: str
    error: str | None = None


@activity.defn
async def check_pr_state(req: CheckPRStateRequest) -> CheckPRStateResult:
    """Poll one PR's merge state. Cheap (single GET) — runs on the
    features queue from the workflow's wait-for-merge loop."""
    conn = await gh.get_active_connection()
    if conn is None:
        return CheckPRStateResult(state="unknown", error="no GitHub connection")
    try:
        state = await gh.get_pull_request_state(
            conn.access_token,
            owner=req.owner,
            repo=req.repo,
            number=req.pr_number,
        )
    except Exception as e:
        log.warning(
            "pr.check_state_failed",
            owner=req.owner,
            repo=req.repo,
            pr=req.pr_number,
            error=str(e),
        )
        return CheckPRStateResult(state="unknown", error=str(e)[:200])
    return CheckPRStateResult(state=state)
