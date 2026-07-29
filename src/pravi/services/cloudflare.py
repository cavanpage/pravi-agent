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

import re
from dataclasses import dataclass, field
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
class PagesDeployment:
    """One entry from the Pages deployments list.

    `url` is the per-commit *atomic* URL (`<hash>.<project>.pages.dev`) —
    that's what the e2e leg tests against, because it's unambiguously the
    build of one specific SHA. `aliases` carries the branch alias, which
    races when two branches sanitize to the same label and is therefore
    display-only.
    """

    id: str
    url: str | None = None
    aliases: list[str] = field(default_factory=list)
    environment: str | None = None
    branch: str | None = None
    commit_hash: str | None = None
    stage_name: str | None = None  # queued|initialize|clone_repo|build|deploy
    stage_status: str | None = None  # success|idle|active|failure|canceled
    created_on: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.stage_name == "deploy" and self.stage_status == "success"

    @property
    def terminal(self) -> bool:
        """True once this deployment can no longer change outcome."""
        return self.succeeded or self.stage_status in ("failure", "canceled")


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
class ZoneRef:
    id: str
    name: str


@dataclass
class CustomDomainStatus:
    """A custom domain attached to a Pages project's PRODUCTION deploy.

    Preview deployments always stay on `*.pages.dev` — per-branch custom
    hostnames need a wildcard-domain setup that isn't a clean API
    operation (ADR 0007).
    """

    hostname: str
    status: str  # initializing|pending|active|deactivated|blocked|error
    url: str | None = None
    verification_data: dict[str, Any] | None = None
    validation_data: dict[str, Any] | None = None
    dns_configured: bool = False
    # Why DNS wasn't written, and the exact record to paste instead.
    dns_skipped_reason: str | None = None
    manual_dns_record: str | None = None


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
            raise RuntimeError(f"cloudflare verify {rv.status_code}: {rv.text[:300]}")
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
            raise RuntimeError(f"cloudflare list-accounts {ra.status_code}: {ra.text[:300]}")
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


async def trigger_pages_deployment(*, name: str, branch: str) -> str | None:
    """Kick a build of `branch` for a git-connected Pages project.
    Returns the deployment id, or None on failure (logged, not raised —
    callers treat the first build as best-effort)."""
    token, account_id = await _require_creds()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/{name}/deployments",
            headers={"Authorization": f"Bearer {token}"},
            data={"branch": branch},
        )
    if r.status_code >= 400:
        log.warning(
            "cloudflare.pages_deploy_trigger_failed",
            name=name,
            status=r.status_code,
            body=r.text[:200],
        )
        return None
    dep_id = (r.json().get("result") or {}).get("id")
    log.info("cloudflare.pages_deploy_triggered", name=name, deployment_id=dep_id)
    return dep_id


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
    log.warning("cloudflare.pages_check_failed", status=r.status_code, body=r.text[:200])
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
                # Pin preview builds on. Cloudflare's default is already
                # "all non-production branches", but leaving it implicit
                # means a project someone tweaked in the dashboard would
                # silently produce zero preview deployments — and the e2e
                # leg would fail with "no deployment appeared" rather than
                # anything that points at the real cause (ADR 0007).
                "preview_deployment_setting": "all",
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
        raise RuntimeError(f"cloudflare create_pages_project {r.status_code}: {r.text[:400]}")
    result = r.json().get("result") or {}
    project_name = result.get("name", name)
    subdomain = result.get("subdomain") or f"{project_name}.pages.dev"
    # Fresh projects return an explicit `"canonical_deployment": null`,
    # which .get(..., {}) does NOT default away — guard the None.
    canonical = (result.get("canonical_deployment") or {}).get("aliases") or []
    canonical_url = canonical[0] if canonical else None
    # The create-repo flow pushes the initial commit BEFORE this project
    # exists, so the git webhook never fires for it — without an explicit
    # kick, the first build only happens on the next push. Best-effort:
    # a failed trigger still leaves a working project.
    try:
        await trigger_pages_deployment(name=project_name, branch=production_branch)
    except Exception:
        log.warning("cloudflare.pages_initial_deploy_trigger_failed", name=project_name)
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


# ---- preview deployments (ADR 0007) --------------------------------------


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ALIAS_SANITIZE = re.compile(r"[^a-z0-9]+")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def pages_branch_alias(branch: str, project: str) -> str:
    """Cloudflare's branch-alias hostname for a branch.

    Lowercased, every run of non-alphanumerics collapsed to one hyphen,
    ends trimmed, label capped at the 63-char DNS limit. `fix/api` on
    project `my-app` → `fix-api.my-app.pages.dev`.

    Informational only: two branches can sanitize to the same label, and
    the alias always points at the *latest* build for it. Testing targets
    the per-commit URL instead.
    """
    label = _ALIAS_SANITIZE.sub("-", branch.lower()).strip("-")[:63].rstrip("-")
    return f"{label}.{project}.pages.dev"


def _parse_deployment(raw: dict[str, Any]) -> PagesDeployment:
    stage = raw.get("latest_stage") or {}
    trigger_meta = (raw.get("deployment_trigger") or {}).get("metadata") or {}
    return PagesDeployment(
        id=raw.get("id") or "",
        url=raw.get("url"),
        aliases=[a for a in (raw.get("aliases") or []) if a],
        environment=raw.get("environment"),
        branch=trigger_meta.get("branch"),
        commit_hash=trigger_meta.get("commit_hash"),
        stage_name=stage.get("name"),
        stage_status=stage.get("status"),
        created_on=raw.get("created_on"),
    )


def match_deployment_by_commit(
    deployments: list[PagesDeployment],
    *,
    commit_sha: str,
    branch: str | None = None,
) -> tuple[PagesDeployment | None, str | None]:
    """Find the deployment built from `commit_sha`. Returns (dep, matched_by).

    Precedence:
      1. `commit_hash` prefix-matches the SHA in either direction — some
         payload versions return an abbreviated hash — case-insensitive.
         `matched_by="commit"`, the only unambiguous match.
      2. Newest deployment on the same branch. `matched_by="branch"`. A
         weaker signal (it could be a *later* push), recorded so callers
         can tell the difference.
      3. Nothing.

    Pure — no HTTP — so it's directly unit-testable.
    """
    sha = (commit_sha or "").strip().lower()
    if sha:
        for dep in deployments:
            h = (dep.commit_hash or "").strip().lower()
            if not h:
                continue
            if h == sha or sha.startswith(h) or h.startswith(sha):
                return dep, "commit"

    if branch:
        on_branch = [d for d in deployments if d.branch == branch]
        if on_branch:
            # `created_on` is ISO-8601 UTC, so lexical max is chronological.
            newest = max(on_branch, key=lambda d: d.created_on or "")
            return newest, "branch"

    return None, None


async def list_preview_deployments(
    *, project: str, per_page: int = 25, page: int = 1
) -> list[PagesDeployment]:
    """Preview-environment deployments for a project, newest first."""
    token, account_id = await _require_creds()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/{project}/deployments",
            headers=_headers(token),
            params={"env": "preview", "per_page": per_page, "page": page},
        )
    if r.status_code == 404:
        # Unknown project — not an error here. The caller's grace period
        # turns this into a specific "check the project name" message.
        log.warning("cloudflare.pages_project_not_found", project=project)
        return []
    if r.status_code >= 400:
        raise RuntimeError(
            f"cloudflare list_deployments {r.status_code}: {r.text[:300]}"
        )
    return [_parse_deployment(d) for d in (r.json().get("result") or []) if d.get("id")]


async def get_deployment_by_commit(
    *,
    project: str,
    commit_sha: str,
    branch: str | None = None,
    max_pages: int = 2,
) -> tuple[PagesDeployment | None, str | None]:
    """Page through preview deployments looking for one built from `commit_sha`.

    A commit match on page 1 short-circuits. A branch match is held back
    until every page has been checked, so an exact commit hit on a later
    page still wins over a weaker branch hit on an earlier one.
    """
    fallback: tuple[PagesDeployment | None, str | None] = (None, None)
    for page in range(1, max_pages + 1):
        deployments = await list_preview_deployments(project=project, page=page)
        if not deployments:
            break
        dep, matched_by = match_deployment_by_commit(
            deployments, commit_sha=commit_sha, branch=branch
        )
        if matched_by == "commit":
            return dep, matched_by
        if matched_by and fallback[0] is None:
            fallback = (dep, matched_by)
    return fallback


async def get_deployment_logs(
    *, project: str, deployment_id: str, tail_lines: int = 200
) -> str:
    """Last N build-log lines, ANSI-stripped and capped.

    Never raises: the repair loop feeds these to the agent after a failed
    build, and losing the logs must not also lose the repair attempt. On
    any failure this returns a `(logs unavailable: …)` marker instead.
    """
    try:
        token, account_id = await _require_creds()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/"
                f"{project}/deployments/{deployment_id}/history/logs",
                headers=_headers(token),
            )
        if r.status_code >= 400:
            return f"(logs unavailable: HTTP {r.status_code})"
        result = r.json().get("result") or {}
        entries = result.get("data") or []
        lines = [str(e.get("line") or "") for e in entries]
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        log.warning(
            "cloudflare.deployment_logs_failed",
            project=project,
            deployment_id=deployment_id,
            error=str(e),
        )
        return f"(logs unavailable: {type(e).__name__}: {e})"

    if not lines:
        return "(logs unavailable: Cloudflare returned no log lines)"
    text = strip_ansi("\n".join(lines[-tail_lines:]))
    return text[-16_000:]


# ---- custom domains (ADR 0007) -------------------------------------------
#
# Permission contract: registering the domain on the Pages project needs
# only `Account → Cloudflare Pages → Edit`, which is what the connect modal
# already asks for. Pointing DNS at it additionally needs `Zone → Read` +
# `Zone → DNS → Edit`. When those are missing NOTHING here raises — the
# domain still gets registered (it just sits in `pending` until DNS
# resolves), and we hand back the exact record for the user to add. A
# degraded-but-useful outcome beats a hard failure on an optional leg.


def _domain_status_from(raw: dict[str, Any], hostname: str) -> CustomDomainStatus:
    status = raw.get("status") or "unknown"
    return CustomDomainStatus(
        hostname=raw.get("name") or hostname,
        status=status,
        url=f"https://{raw.get('name') or hostname}",
        verification_data=raw.get("verification_data"),
        validation_data=raw.get("validation_data"),
    )


async def attach_custom_domain(*, project: str, hostname: str) -> CustomDomainStatus:
    """Register `hostname` on a Pages project.

    An "already exists" response is success — the whole flow is meant to
    be re-runnable.
    """
    token, account_id = await _require_creds()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/{project}/domains",
            headers=_headers(token),
            json={"name": hostname},
        )
    if r.status_code < 400:
        result = r.json().get("result") or {}
        log.info("cloudflare.custom_domain_attached", project=project, hostname=hostname)
        return _domain_status_from(result, hostname)

    body = r.text[:400]
    if r.status_code == 409 or "already" in body.lower():
        existing = await get_custom_domain(project=project, hostname=hostname)
        if existing is not None:
            return existing
        return CustomDomainStatus(hostname=hostname, status="pending")
    raise RuntimeError(f"cloudflare attach_custom_domain {r.status_code}: {body}")


async def get_custom_domain(
    *, project: str, hostname: str
) -> CustomDomainStatus | None:
    """Current state of one custom domain on a project, or None."""
    token, account_id = await _require_creds()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{_CF_API_BASE}/accounts/{account_id}/pages/projects/{project}/domains",
            headers=_headers(token),
        )
    if r.status_code >= 400:
        return None
    for raw in r.json().get("result") or []:
        if (raw.get("name") or "").lower() == hostname.lower():
            return _domain_status_from(raw, hostname)
    return None


def _zone_candidates(hostname: str) -> list[str]:
    """Registrable-zone guesses for a hostname, longest suffix first.

    `app.staging.example.com` → staging.example.com, example.com. Two
    labels is the shortest thing that can be a zone.
    """
    labels = hostname.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


async def find_zone_for_hostname(hostname: str) -> ZoneRef | None:
    """The Cloudflare zone this hostname belongs to, or None.

    Returns None — never raises — when the token can't read zones, so the
    caller degrades to a manual DNS instruction.
    """
    try:
        token, _account_id = await _require_creds()
    except CloudflareNotConfigured:
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for candidate in _zone_candidates(hostname):
            r = await client.get(
                f"{_CF_API_BASE}/zones",
                headers=_headers(token),
                params={"name": candidate},
            )
            if r.status_code in (401, 403):
                log.info("cloudflare.zone_lookup_forbidden", hostname=hostname)
                return None
            if r.status_code >= 400:
                continue
            for z in r.json().get("result") or []:
                if z.get("id"):
                    return ZoneRef(id=z["id"], name=z.get("name") or candidate)
    return None


async def ensure_cname(
    *, zone_id: str, hostname: str, target: str, proxied: bool = True
) -> tuple[bool, str | None]:
    """Point `hostname` at `target` with a proxied CNAME.

    Idempotent: an existing CNAME to the same target is a no-op success.
    Returns (ok, skipped_reason); never raises on a permission problem.
    """
    try:
        token, _account_id = await _require_creds()
    except CloudflareNotConfigured as e:
        return False, str(e)

    async with httpx.AsyncClient(timeout=30.0) as client:
        existing = await client.get(
            f"{_CF_API_BASE}/zones/{zone_id}/dns_records",
            headers=_headers(token),
            params={"name": hostname},
        )
        if existing.status_code in (401, 403):
            return False, _DNS_SCOPE_HINT
        if existing.status_code < 400:
            for rec in existing.json().get("result") or []:
                if rec.get("type") == "CNAME" and (rec.get("content") or "").rstrip(
                    "."
                ) == target.rstrip("."):
                    log.info("cloudflare.cname_already_set", hostname=hostname)
                    return True, None

        r = await client.post(
            f"{_CF_API_BASE}/zones/{zone_id}/dns_records",
            headers=_headers(token),
            json={
                "type": "CNAME",
                "name": hostname,
                "content": target,
                "proxied": proxied,
                "comment": "created by pravi for a Cloudflare Pages custom domain",
            },
        )
    if r.status_code in (401, 403):
        return False, _DNS_SCOPE_HINT
    if r.status_code >= 400:
        return False, f"could not create the DNS record: {r.text[:300]}"
    log.info("cloudflare.cname_created", hostname=hostname, target=target)
    return True, None


_DNS_SCOPE_HINT = (
    "the Cloudflare token lacks Zone:Read + Zone:DNS:Edit, so DNS wasn't "
    "pointed at the Pages project. The domain is registered and will go "
    "live once the record below exists — add it yourself, or recreate the "
    "token with those permissions and retry."
)


async def setup_custom_domain(
    *, project: str, hostname: str
) -> CustomDomainStatus:
    """Register a custom domain AND point DNS at it, best-effort.

    Composes the three calls above. The Pages registration is the part
    that must work; DNS is opportunistic, and when it can't be written the
    result carries a copy-pasteable record instead.
    """
    status = await attach_custom_domain(project=project, hostname=hostname)
    target = f"{project}.pages.dev"

    zone = await find_zone_for_hostname(hostname)
    if zone is None:
        status.dns_skipped_reason = (
            f"no Cloudflare zone found for {hostname!r}, or the token cannot "
            f"read zones. {_DNS_SCOPE_HINT}"
        )
    else:
        ok, reason = await ensure_cname(
            zone_id=zone.id, hostname=hostname, target=target
        )
        status.dns_configured = ok
        status.dns_skipped_reason = reason

    if not status.dns_configured:
        status.manual_dns_record = f"{hostname}  CNAME  {target}  (proxied)"
    else:
        # DNS just changed; re-read so the reported status reflects it.
        refreshed = await get_custom_domain(project=project, hostname=hostname)
        if refreshed is not None:
            refreshed.dns_configured = True
            status = refreshed
    return status
