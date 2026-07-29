"""Ephemeral preview deployments on Cloudflare Pages (ADR 0007).

Three activities, all cheap and all on the `features` queue:

  - `load_preview_config` — read the branch's `.builder/domains.yaml`
    through the sandbox and resolve which Pages project to watch.
  - `poll_preview_deployment` — ONE Cloudflare API call. The waiting is
    done by a `workflow.sleep` loop in `FeatureWorkflow`, not here: a
    15-minute heartbeating activity restarts from zero if its worker
    dies, whereas a timer loop resumes from the next poll with every
    prior result already in workflow history.
  - `fetch_deployment_logs` — build logs for a failed deploy, so the
    repair prompt can say what actually broke.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
import yaml
from temporalio import activity

from pravi.agents.sandbox.factory import get_sandbox
from pravi.agents.sandbox.protocols import SandboxExecRequest, SandboxHandle
from pravi.db.models import Repo
from pravi.db.session import session_scope
from pravi.domains.registry import DomainsFile
from pravi.services import cloudflare as cf

log = structlog.get_logger(__name__)


@dataclass
class PreviewSnapshot:
    """Resolved, flattened preview config for one run.

    Snapshotted at the activity boundary — same discipline as
    `DevActivityRequest` — so a mid-run edit to domains.yaml can't change
    the meaning of a workflow already in flight.
    """

    project: str
    provider: str
    wait_timeout_seconds: int
    first_deployment_grace_seconds: int
    e2e_dir: str
    e2e_install: list[str]
    e2e_browsers: list[str]
    e2e_command: list[str]
    e2e_base_url_env: str
    e2e_timeout_seconds: int
    # "domains.yaml" | "repo_row" | "name_probe" — surfaced in the
    # "why is e2e off / pointed there?" event.
    project_source: str = "domains.yaml"


@dataclass
class LoadPreviewConfigRequest:
    handle: SandboxHandle
    repo_id: int


@dataclass
class PollPreviewRequest:
    project: str
    commit_sha: str
    branch: str | None = None


@dataclass
class PreviewDeployment:
    """Workflow-facing view of one poll."""

    found: bool = False
    deployment_id: str | None = None
    url: str | None = None  # per-commit atomic URL — what we test
    alias_url: str | None = None  # branch alias — display only
    stage_name: str | None = None
    stage_status: str | None = None
    terminal: bool = False
    succeeded: bool = False
    matched_by: str | None = None  # "commit" | "branch"
    error: str | None = None  # transport error, distinct from a failed build


@dataclass
class DeploymentLogsRequest:
    project: str
    deployment_id: str
    tail_lines: int = 200


async def _resolve_project(
    repo_id: int, configured: str | None
) -> tuple[str | None, str]:
    """Which Pages project builds this repo?

    Precedence: explicit config → the persisted Repo row → a one-shot
    name probe against Cloudflare (written back on a hit, so it costs one
    API call per repo, ever).
    """
    if configured:
        return configured, "domains.yaml"

    async with session_scope() as session:
        repo = await session.get(Repo, repo_id)
        if repo is None:
            return None, "unknown_repo"
        if repo.cf_pages_project:
            return repo.cf_pages_project, "repo_row"
        candidate = repo.github_name or repo.name

    if not candidate:
        return None, "no_candidate"
    try:
        exists = await cf.pages_project_exists(candidate)
    except cf.CloudflareNotConfigured:
        return None, "cloudflare_not_configured"
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        log.warning("preview.project_probe_failed", repo_id=repo_id, error=str(e))
        return None, "probe_failed"
    if not exists:
        return None, "no_project"

    async with session_scope() as session:
        repo = await session.get(Repo, repo_id)
        if repo is not None and not repo.cf_pages_project:
            repo.cf_pages_project = candidate
    return candidate, "name_probe"


@activity.defn
async def load_preview_config(
    req: LoadPreviewConfigRequest,
) -> PreviewSnapshot | None:
    """Read the preview block off the BRANCH and resolve its Pages project.

    Reading through the sandbox rather than the main checkout means a PR
    that adds the `preview:` block is honored on the very same run, and
    keeps ADR 0003's "no host filesystem assumptions" intact.

    Returns None when the leg is off — no config, or no project to watch.
    """
    sandbox = get_sandbox()
    result = await sandbox.exec(
        req.handle,
        SandboxExecRequest(command=["cat", ".builder/domains.yaml"], timeout_seconds=30),
    )
    if result.exit_code != 0:
        log.info("preview.no_domains_yaml", sandbox_id=req.handle.sandbox_id)
        return None

    try:
        parsed = DomainsFile.model_validate(yaml.safe_load(result.stdout))
    except Exception as e:  # noqa: BLE001 — a broken config disables the leg
        log.warning("preview.domains_yaml_invalid", error=str(e))
        return None

    preview = parsed.preview
    if preview is None:
        log.info("preview.not_configured", sandbox_id=req.handle.sandbox_id)
        return None

    project, source = await _resolve_project(req.repo_id, preview.project)
    if project is None:
        log.info("preview.no_project", repo_id=req.repo_id, reason=source)
        return None

    return PreviewSnapshot(
        project=project,
        provider=preview.provider,
        wait_timeout_seconds=preview.wait_timeout_seconds,
        first_deployment_grace_seconds=preview.first_deployment_grace_seconds,
        e2e_dir=preview.e2e.dir,
        e2e_install=list(preview.e2e.install),
        e2e_browsers=list(preview.e2e.browsers),
        e2e_command=list(preview.e2e.command),
        e2e_base_url_env=preview.e2e.base_url_env,
        e2e_timeout_seconds=preview.e2e.timeout_seconds,
        project_source=source,
    )


@activity.defn
async def poll_preview_deployment(req: PollPreviewRequest) -> PreviewDeployment:
    """One look at Cloudflare. Never raises for "not there yet"."""
    try:
        dep, matched_by = await cf.get_deployment_by_commit(
            project=req.project, commit_sha=req.commit_sha, branch=req.branch
        )
    except cf.CloudflareNotConfigured as e:
        return PreviewDeployment(found=False, terminal=True, error=str(e))
    if dep is None:
        return PreviewDeployment(found=False)

    alias = next(
        (a for a in dep.aliases if a.startswith("https://")),
        None,
    )
    return PreviewDeployment(
        found=True,
        deployment_id=dep.id,
        url=dep.url,
        alias_url=alias,
        stage_name=dep.stage_name,
        stage_status=dep.stage_status,
        terminal=dep.terminal,
        succeeded=dep.succeeded,
        matched_by=matched_by,
    )


@activity.defn
async def fetch_deployment_logs(req: DeploymentLogsRequest) -> str:
    """Build-log tail for a failed deploy. Returns a marker string rather
    than raising — losing the logs must not also lose the repair attempt."""
    return await cf.get_deployment_logs(
        project=req.project,
        deployment_id=req.deployment_id,
        tail_lines=req.tail_lines,
    )


@dataclass
class RecordPreviewOutcomeRequest:
    """Write the ticket-level report fields the UI reads."""

    ticket_id: int
    preview_url: str | None = None
    e2e_verdict: str | None = None


@activity.defn
async def record_preview_outcome(req: RecordPreviewOutcomeRequest) -> None:
    from pravi.db.models import Ticket

    async with session_scope() as session:
        ticket = await session.get(Ticket, req.ticket_id)
        if ticket is None:
            return
        if req.preview_url is not None:
            ticket.preview_url = req.preview_url
        if req.e2e_verdict is not None:
            ticket.e2e_verdict = req.e2e_verdict


__all__ = [
    "DeploymentLogsRequest",
    "LoadPreviewConfigRequest",
    "PollPreviewRequest",
    "PreviewDeployment",
    "PreviewSnapshot",
    "RecordPreviewOutcomeRequest",
    "fetch_deployment_logs",
    "load_preview_config",
    "poll_preview_deployment",
    "record_preview_outcome",
]
