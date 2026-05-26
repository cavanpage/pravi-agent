"""REST + SSE routes for the plan-review UI."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased
from sse_starlette.sse import EventSourceResponse
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError

from pravi.agents.architects.decompose_parser import parse_decomposition
from pravi.agents.factory import get_architect
from pravi.agents.protocols import (
    ArchitectRequest,
    ClarificationQA,
    ClarifyRequest,
    DecomposeRequest,
    DomainBrief,
)
from pravi.api.schemas import (
    AddDependencyRequest,
    BudgetBreakdownOut,
    BulkDeleteRequest,
    BulkDeleteResult,
    ClarificationQuestionOut,
    ClarifyDraftOut,
    CostRollupOut,
    CreateTicketRequest,
    CreateTicketResult,
    DecomposeApproveOut,
    DecomposeApproveRequest,
    DecomposedFeatureOut,
    DecomposeDraftOut,
    DecomposeDraftRequest,
    DecomposedTaskOut,
    DomainOut,
    PersistedClarificationOut,
    PlanApproveOut,
    PlanApproveRequest,
    PlanDraftOut,
    PlanDraftRequest,
    RepoOut,
    RoadmapFeatureOut,
    RoadmapOut,
    RoadmapWaveOut,
    RunEventOut,
    RunOut,
    TicketBudgetUpdate,
    TicketOut,
    WorkflowStatusEvent,
)
from pravi.api.temporal_client import get_temporal_client
from pravi.budget import cost_rollup
from pravi.config import get_settings
from pravi.db.models import (
    Clarification,
    Event,
    FeatureDependency,
    Plan,
    Repo,
    Run,
    Ticket,
    TicketKind,
    TicketStatus,
)
from pravi.db.session import session_scope
from pravi.domains.registry import DomainRegistry
from pravi.events import KIND_RUN_FINISHED, listen_events
from pravi.services import clarification as clarification_service
from pravi.services import github as gh
from pravi.temporal_utils import (
    DOMAIN,
    PRAVI_STATUS,
    REPO_NAME,
    TICKET_ID,
    feature_workflow_id,
    repo_slug,
)
from pravi.workflows.feature_workflow import FeatureWorkflow, FeatureWorkflowInput

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api")


def _resolve_domains_file(repo_root: Path, override: str | None) -> Path | None:
    if not override:
        return None
    return Path(override).expanduser().resolve()


async def _get_ticket_and_repo(external_id: str) -> tuple[Ticket, Repo]:
    async with session_scope() as session:
        stmt = (
            select(Ticket, Repo)
            .join(Repo, Ticket.repo_id == Repo.id)
            .where(Ticket.external_id == external_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"ticket {external_id!r} not found")
        ticket, repo = row
        # Detach copies so the caller can use them after the session closes.
        session.expunge(ticket)
        session.expunge(repo)
        return ticket, repo


async def _parent_external_id(session, parent_id: int | None) -> str | None:
    if parent_id is None:
        return None
    return (
        await session.execute(
            select(Ticket.external_id).where(Ticket.id == parent_id)
        )
    ).scalar_one_or_none()


def _ticket_to_out(
    ticket: Ticket,
    repo: Repo,
    *,
    parent_external_id: str | None = None,
    child_count: int = 0,
) -> TicketOut:
    pr_url: str | None = None
    if ticket.pr_number and repo.github_owner and repo.github_name:
        pr_url = (
            f"https://github.com/{repo.github_owner}/{repo.github_name}"
            f"/pull/{ticket.pr_number}"
        )
    return TicketOut(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        body=ticket.body or "",
        domain_name=ticket.domain_name,
        status=str(ticket.status),
        workflow_id=ticket.workflow_id,
        repo=RepoOut(
            id=repo.id,
            name=repo.name,
            local_path=repo.local_path,
            github_owner=repo.github_owner,
            github_name=repo.github_name,
        ),
        kind=str(ticket.kind),
        parent_external_id=parent_external_id,
        child_count=child_count,
        cost_ceiling_usd=ticket.cost_ceiling_usd,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        pr_number=ticket.pr_number,
        pr_url=pr_url,
    )


@router.get("/tickets", response_model=list[TicketOut])
async def list_tickets(
    status: str | None = None,
    kind: str | None = None,
    parent_external_id: str | None = None,
    limit: int = 100,
) -> list[TicketOut]:
    """List tickets, most-recently-updated first.

    Filters:
      - `?status=planning` — only those waiting for a human-approved plan.
      - `?kind=epic|feature|task` — single layer of the hierarchy.
      - `?parent_external_id=ABC-99` — children of a specific ticket.
    """
    async with session_scope() as session:
        ParentT = aliased(Ticket)
        ChildCount = (
            select(Ticket.parent_id, func.count(Ticket.id).label("n"))
            .where(Ticket.parent_id.is_not(None))
            .group_by(Ticket.parent_id)
            .subquery()
        )
        stmt = (
            select(Ticket, Repo, ParentT.external_id, ChildCount.c.n)
            .join(Repo, Ticket.repo_id == Repo.id)
            .outerjoin(ParentT, Ticket.parent_id == ParentT.id)
            .outerjoin(ChildCount, ChildCount.c.parent_id == Ticket.id)
        )
        if status:
            stmt = stmt.where(Ticket.status == status)
        if kind:
            stmt = stmt.where(Ticket.kind == kind)
        if parent_external_id:
            # Children of the named parent.
            stmt = stmt.where(ParentT.external_id == parent_external_id)
        stmt = stmt.order_by(Ticket.updated_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).all()
        return [
            _ticket_to_out(
                t, r, parent_external_id=pext, child_count=int(cc or 0)
            )
            for t, r, pext, cc in rows
        ]


@router.get("/tickets/{external_id}", response_model=TicketOut)
async def get_ticket(external_id: str) -> TicketOut:
    ticket, repo = await _get_ticket_and_repo(external_id)
    async with session_scope() as session:
        pext = await _parent_external_id(session, ticket.parent_id)
        cc = (
            await session.execute(
                select(func.count(Ticket.id)).where(Ticket.parent_id == ticket.id)
            )
        ).scalar_one()
    return _ticket_to_out(ticket, repo, parent_external_id=pext, child_count=int(cc))


@router.get("/tickets/{external_id}/children", response_model=list[TicketOut])
async def list_children(external_id: str) -> list[TicketOut]:
    """Direct children of a ticket (epic → features, feature → tasks)."""
    return await list_tickets(parent_external_id=external_id, limit=500)


@router.get("/tickets/{external_id}/cost-rollup", response_model=CostRollupOut)
async def get_cost_rollup(external_id: str) -> CostRollupOut:
    """Full budget picture for one ticket — own ceiling/spend + each ancestor.

    Powers the <BudgetMeter> on the ticket page. Computed at query time
    (no caching) — fine because rollups touch at most a few dozen events
    per ticket subtree.
    """
    ticket, _repo = await _get_ticket_and_repo(external_id)
    async with session_scope() as session:
        # Re-fetch in this session so relationships and updates are usable.
        fresh = await session.get(Ticket, ticket.id)
        if fresh is None:
            raise HTTPException(status_code=404, detail=f"ticket {external_id!r} not found")
        rollup = await cost_rollup(session, fresh)
    return CostRollupOut(
        ticket_id=rollup.ticket_id,
        external_id=rollup.external_id,
        kind=rollup.kind,
        own_ceiling_usd=rollup.own_ceiling_usd,
        own_spent_usd=rollup.own_spent_usd,
        effective_remaining_usd=rollup.effective_remaining_usd,
        constraint_source=rollup.constraint_source,
        chain=[
            BudgetBreakdownOut(
                ticket_id=b.ticket_id,
                external_id=b.external_id,
                kind=b.kind,
                title=b.title,
                own_ceiling_usd=b.own_ceiling_usd,
                spent_usd=b.spent_usd,
                remaining_usd=b.remaining_usd,
            )
            for b in rollup.chain
        ],
    )


@router.patch("/tickets/{external_id}/budget", response_model=TicketOut)
async def update_ticket_budget(
    external_id: str, body: TicketBudgetUpdate
) -> TicketOut:
    """Set or clear the per-ticket cost ceiling. Null = revert to inheritance."""
    if body.cost_ceiling_usd is not None and body.cost_ceiling_usd < 0:
        raise HTTPException(status_code=400, detail="cost_ceiling_usd cannot be negative")
    ticket, repo = await _get_ticket_and_repo(external_id)
    async with session_scope() as session:
        fresh = await session.get(Ticket, ticket.id)
        if fresh is None:
            raise HTTPException(status_code=404, detail=f"ticket {external_id!r} not found")
        fresh.cost_ceiling_usd = body.cost_ceiling_usd
        await session.flush()
        pext = await _parent_external_id(session, fresh.parent_id)
        cc = (
            await session.execute(
                select(func.count(Ticket.id)).where(Ticket.parent_id == fresh.id)
            )
        ).scalar_one()
        out_ticket = fresh
        session.expunge(out_ticket)
    return _ticket_to_out(out_ticket, repo, parent_external_id=pext, child_count=int(cc))


@router.get("/tickets/{external_id}/domains", response_model=list[DomainOut])
async def list_domains_for_ticket(
    external_id: str, domains_file: str | None = None
) -> list[DomainOut]:
    _, repo = await _get_ticket_and_repo(external_id)
    return _domains_at(Path(repo.local_path), domains_file)


def _domains_at(repo_root: Path, domains_file: str | None) -> list[DomainOut]:
    if not repo_root.is_dir():
        raise HTTPException(status_code=400, detail=f"repo path {repo_root} does not exist")
    try:
        registry = DomainRegistry.load(
            repo_root, override_file=_resolve_domains_file(repo_root, domains_file)
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return [
        DomainOut(
            name=d.name,
            description=d.description,
            paths=list(d.paths),
            test=d.test,
            build=d.build,
        )
        for d in registry.domains
    ]


@router.get("/repos", response_model=list[RepoOut])
async def list_repos() -> list[RepoOut]:
    """List repos pravi has seen — useful for the create-ticket dropdown.

    Includes both repos with existing tickets AND repos configured via
    PRAVI_TARGET_REPOS (so a fresh install has something to offer in the UI).
    """
    seen: dict[str, RepoOut] = {}
    async with session_scope() as session:
        rows = (await session.execute(select(Repo))).scalars().all()
        for r in rows:
            seen[r.local_path] = RepoOut(id=r.id, name=r.name, local_path=r.local_path)

    for path in get_settings().target_repos:
        resolved = str(path.expanduser().resolve())
        if resolved not in seen and Path(resolved).is_dir():
            seen[resolved] = RepoOut(id=-1, name=Path(resolved).name, local_path=resolved)
    return list(seen.values())


@router.get("/repos/_/domains", response_model=list[DomainOut])
async def list_domains_for_path(
    repo_path: str,
    domains_file: str | None = None,
) -> list[DomainOut]:
    """Domain list for a repo we may or may not have a ticket for yet —
    used by the create-ticket form to populate the domain dropdown."""
    return _domains_at(Path(repo_path).expanduser().resolve(), domains_file)


_VALID_PARENT_KIND = {
    TicketKind.epic: None,  # epics have no parent
    TicketKind.feature: TicketKind.epic,  # features parent to epics
    TicketKind.task: TicketKind.feature,  # tasks parent to features (or None)
}


@router.post("/tickets", response_model=CreateTicketResult, status_code=201)
async def create_ticket(req: CreateTicketRequest) -> CreateTicketResult:
    """Create (or upsert) a Ticket row.

    For kind=task, also launches a FeatureWorkflow (workflow lands in
    `waiting_for_plan`). For kind=epic / kind=feature, just stores the row —
    those are organizational containers; epic auto-decomposition is a
    separate workflow type planned for a follow-up slice.

    Hierarchy + inheritance:
      - parent_external_id can be set to put this ticket under another.
      - Hierarchy rules are: epic → feature → task. Mismatches → 400.
      - If a parent is given, repo / domain are inherited unless overridden.
    """
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title is required")

    try:
        kind = TicketKind(req.kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid kind: {req.kind!r}") from e

    # ----- Resolve parent (if any) -----
    parent_row: Ticket | None = None
    parent_repo: Repo | None = None
    if req.parent_external_id:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(Ticket, Repo)
                    .join(Repo, Ticket.repo_id == Repo.id)
                    .where(Ticket.external_id == req.parent_external_id)
                )
            ).one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"parent {req.parent_external_id!r} not found",
                )
            parent_row, parent_repo = row
            session.expunge(parent_row)
            session.expunge(parent_repo)

    expected_parent_kind = _VALID_PARENT_KIND[kind]
    if expected_parent_kind is None and parent_row is not None:
        raise HTTPException(
            status_code=400,
            detail=f"a {kind.value} cannot have a parent",
        )
    if expected_parent_kind is not None and parent_row is not None:
        # Compare TicketKind enum semantically; SQLA returns str on read.
        if str(parent_row.kind) != expected_parent_kind.value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"a {kind.value}'s parent must be a {expected_parent_kind.value}; "
                    f"got {parent_row.kind}"
                ),
            )

    # ----- Resolve repo path (inherit from parent / clone from GitHub) -----
    repo_path = req.repo_path
    if repo_path is None and parent_repo is not None:
        repo_path = parent_repo.local_path
    # If the user picked a repo from the GitHub search, lazily clone it now.
    # Inherited repos win over a github_repo pick (parent's repo is the source
    # of truth for the subtree).
    github_meta: tuple[str, str] | None = None
    if req.github_repo is not None and parent_repo is None:
        conn = await gh.get_active_connection()
        if conn is None:
            raise HTTPException(
                status_code=401,
                detail="github_repo provided but no active GitHub connection",
            )
        try:
            cloned = await gh.ensure_repo_cloned(
                owner=req.github_repo.owner,
                name=req.github_repo.name,
                clone_url=req.github_repo.clone_url,
                access_token=conn.access_token,
                base_dir=get_settings().clone_base_resolved,
            )
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"github clone failed: {e}"
            ) from e
        repo_path = str(cloned)
        github_meta = (req.github_repo.owner, req.github_repo.name)
    if not repo_path:
        raise HTTPException(
            status_code=400,
            detail="repo_path or github_repo is required (or provide parent_external_id to inherit)",
        )
    repo_root = Path(repo_path).expanduser().resolve()
    if not repo_root.is_dir() or not (repo_root / ".git").is_dir():
        raise HTTPException(status_code=400, detail=f"not a git repo: {repo_root}")
    if parent_repo is not None and parent_repo.local_path != str(repo_root):
        raise HTTPException(
            status_code=400,
            detail=(
                f"repo_path mismatch: parent uses {parent_repo.local_path}, "
                f"got {repo_root}"
            ),
        )

    # ----- Resolve domain (inherit from parent if missing) -----
    # Epics don't need a domain (they can span); features + tasks do.
    chosen_name: str | None = req.domain_name
    if chosen_name is None and parent_row is not None and parent_row.domain_name:
        chosen_name = parent_row.domain_name

    chosen = None
    if kind in (TicketKind.feature, TicketKind.task):
        try:
            registry = DomainRegistry.load(repo_root)
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        chosen = registry.get(chosen_name) if chosen_name else registry.domains[0]
        chosen_name = chosen.name

    external_id = req.external_id.strip() if req.external_id else ""
    if not external_id:
        prefix = {"epic": "e-", "feature": "f-", "task": "t-"}[kind.value]
        external_id = f"{prefix}{uuid.uuid4().hex[:8]}"

    async with session_scope() as session:
        existing_repo = (
            await session.execute(select(Repo).where(Repo.local_path == str(repo_root)))
        ).scalar_one_or_none()
        if existing_repo is None:
            existing_repo = Repo(name=repo_root.name, local_path=str(repo_root))
            session.add(existing_repo)
            await session.flush()
        # If we just cloned from GitHub, stamp owner/name so push_and_open_pr
        # can build https://github.com/<owner>/<name> URLs later without
        # re-parsing the remote.
        if github_meta is not None and (
            existing_repo.github_owner is None or existing_repo.github_name is None
        ):
            existing_repo.github_owner, existing_repo.github_name = github_meta

        existing_ticket = (
            await session.execute(
                select(Ticket).where(
                    Ticket.repo_id == existing_repo.id,
                    Ticket.external_id == external_id,
                )
            )
        ).scalar_one_or_none()

        if existing_ticket is None:
            ticket = Ticket(
                repo_id=existing_repo.id,
                external_id=external_id,
                title=req.title,
                body=req.body,
                domain_name=chosen_name,
                status=TicketStatus.pending,
                kind=kind,
                parent_id=parent_row.id if parent_row else None,
                cost_ceiling_usd=req.cost_ceiling_usd,
            )
            session.add(ticket)
            await session.flush()
            ticket_id = ticket.id
        else:
            # Idempotent upsert — only edit malleable fields, don't reshape hierarchy.
            existing_ticket.title = req.title
            existing_ticket.body = req.body
            if chosen_name:
                existing_ticket.domain_name = chosen_name
            # Allow updating the ceiling on re-create; null clears it.
            existing_ticket.cost_ceiling_usd = req.cost_ceiling_usd
            ticket_id = existing_ticket.id

    settings = get_settings()
    web_url = f"{settings.web_url_base.rstrip('/')}/tickets/{external_id}"

    # Epics + features don't run workflows in Phase 1.
    if kind != TicketKind.task:
        # Auto-kick a clarify run for fresh epics so the architect's questions
        # are ready (or in flight) by the time the user lands on the epic
        # page. Survives navigating away — see services/clarification.py.
        if kind == TicketKind.epic and existing_ticket is None:
            try:
                await clarification_service.kickoff_clarification(ticket_id)
            except Exception as e:
                # Non-fatal — surface in logs but still return the created
                # epic. User can manually re-run from the UI.
                log.warning(
                    "clarification.kickoff_failed",
                    ticket_id=ticket_id,
                    error=str(e),
                )
        return CreateTicketResult(
            external_id=external_id,
            ticket_id=ticket_id,
            workflow_id=None,
            web_url=web_url,
        )

    client = await get_temporal_client()
    inp = FeatureWorkflowInput(
        ticket_id=ticket_id,
        domain_name=chosen.name,
        domain_description=chosen.description,
        domain_paths=list(chosen.paths),
        base_ref=req.base_ref,
        llm_task_queue=settings.temporal_task_queue_llm,
        cleanup_worktree=req.cleanup_worktree,
    )
    workflow_id = feature_workflow_id(repo_root, external_id)
    try:
        await client.start_workflow(
            FeatureWorkflow.run,
            inp,
            id=workflow_id,
            task_queue=settings.temporal_task_queue_features,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            search_attributes=TypedSearchAttributes(
                [
                    SearchAttributePair(REPO_NAME, repo_slug(repo_root)),
                    SearchAttributePair(DOMAIN, chosen.name),
                    SearchAttributePair(TICKET_ID, external_id),
                    SearchAttributePair(PRAVI_STATUS, "waiting_for_plan"),
                ]
            ),
        )
    except RPCError as e:
        if "WorkflowExecutionAlreadyStarted" in str(e):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workflow {workflow_id} is already running. "
                    f"Cancel it or wait for it to finish before re-creating."
                ),
            ) from e
        raise

    return CreateTicketResult(
        external_id=external_id,
        ticket_id=ticket_id,
        workflow_id=workflow_id,
        web_url=web_url,
    )


@router.post("/tickets/{external_id}/plan/draft", response_model=PlanDraftOut)
async def draft(external_id: str, req: PlanDraftRequest) -> PlanDraftOut:
    """Invoke the architect agent and return a fresh draft plan.

    Does NOT persist — the UI calls /plan/approve once the user is happy.
    """
    ticket, repo = await _get_ticket_and_repo(external_id)

    domain_name = req.domain_name or ticket.domain_name
    if not domain_name:
        raise HTTPException(status_code=400, detail="ticket has no domain and none was specified")

    registry = DomainRegistry.load(
        Path(repo.local_path),
        override_file=_resolve_domains_file(Path(repo.local_path), req.domains_file),
    )
    try:
        chosen = registry.get(domain_name)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Pull ancestry so the architect sees epic/feature context for hierarchical tickets.
    from pravi.activities.db_activity import _load_ancestors, build_ancestral_body

    async with session_scope() as session:
        db_ticket = await session.get(Ticket, ticket.id)
        ancestors = (
            await _load_ancestors(session, db_ticket) if db_ticket else []
        )
    merged_body = build_ancestral_body(
        ancestors,
        str(ticket.kind),
        ticket.title,
        ticket.body or "",
    )

    settings = get_settings()
    arch_req = ArchitectRequest(
        repo_path=repo.local_path,
        repo_name=repo.name,
        domain_name=chosen.name,
        domain_description=chosen.description,
        domain_paths=list(chosen.paths),
        ticket_title=ticket.title,
        ticket_body=merged_body,
        domain_context_files=list(chosen.context_files),
        max_wall_seconds=settings.architect_max_wall_seconds,
        max_turns=settings.architect_max_turns,
        max_cost_usd=settings.architect_max_cost_usd,
    )
    arch_result = await get_architect().draft_plan(arch_req)
    if not arch_result.success:
        raise HTTPException(
            status_code=500,
            detail=f"architect failed: {'; '.join(arch_result.errors) or 'no plan produced'}",
        )
    return PlanDraftOut(
        plan_md=arch_result.plan_md,
        prompt_version=arch_result.prompt_version,
        num_turns=arch_result.num_turns,
        duration_ms=arch_result.duration_ms,
        total_cost_usd=arch_result.total_cost_usd,
        domain_name=chosen.name,
    )


@router.post("/tickets/{external_id}/plan/approve", response_model=PlanApproveOut)
async def approve(external_id: str, req: PlanApproveRequest) -> PlanApproveOut:
    """Persist a Plan row and signal the waiting FeatureWorkflow."""
    if not req.content_md.strip():
        raise HTTPException(status_code=400, detail="plan content is empty")

    ticket, repo = await _get_ticket_and_repo(external_id)

    registry = DomainRegistry.load(
        Path(repo.local_path),
        override_file=_resolve_domains_file(Path(repo.local_path), req.domains_file),
    )
    try:
        chosen = registry.get(req.domain_name)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Check the workflow is alive before we save anything.
    client = await get_temporal_client()
    workflow_id = feature_workflow_id(Path(repo.local_path), external_id)
    try:
        desc = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as e:
        if "NotFound" in str(e) or "not found" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workflow {workflow_id} does not exist — start it first "
                    f"with `pravi ticket start {external_id} ...`"
                ),
            ) from e
        raise
    if int(desc.status) != 1:  # RUNNING
        raise HTTPException(
            status_code=409,
            detail=f"workflow {workflow_id} is not running (status={desc.status})",
        )

    async with session_scope() as session:
        plan_row = Plan(
            ticket_id=ticket.id,
            domain_name=chosen.name,
            domain_snapshot=chosen.model_dump(),
            content_md=req.content_md,
            approved_at=datetime.now(UTC),
            approved_by=req.approver or os.environ.get("USER") or "web-ui",
        )
        session.add(plan_row)
        await session.flush()
        plan_id = plan_row.id

    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(FeatureWorkflow.approve_plan, plan_id)
    log.info("plan.approved_via_web", external_id=external_id, plan_id=plan_id)

    return PlanApproveOut(plan_id=plan_id, signalled=True, workflow_id=workflow_id)


@router.post("/tickets/{external_id}/clarify", response_model=ClarifyDraftOut)
async def clarify(external_id: str) -> ClarifyDraftOut:
    """Run the architect in 'clarify' mode — ask 2–5 targeted questions about
    an epic before decomposing. Cheap (~$0.02–0.10), short.

    Returns an empty `questions` list if the architect thinks nothing's worth
    asking. The UI should let the user proceed directly to decomposition in
    that case.
    """
    log.info("clarify.requested", external_id=external_id)
    ticket, repo = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400,
            detail=f"only epics can be clarified (got kind={ticket.kind})",
        )

    repo_root = Path(repo.local_path)
    try:
        registry = DomainRegistry.load(repo_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    available = [
        DomainBrief(name=d.name, description=d.description, paths=list(d.paths))
        for d in registry.domains
    ]
    context_files: list[str] = []
    for d in registry.domains:
        context_files.extend(d.context_files)

    settings = get_settings()
    req = ClarifyRequest(
        repo_path=repo.local_path,
        repo_name=repo.name,
        epic_title=ticket.title,
        epic_body=ticket.body or "",
        available_domains=available,
        default_domain=ticket.domain_name,
        domain_context_files=context_files,
        # Clarify is a small, cheap call — cap the wall-clock low so a stuck
        # SDK doesn't leave the UI button spinning for the full 5-minute
        # architect default. Turn cap is also tight: clarify is meant to
        # skim + ask 2-5 questions, not deep-explore the repo. 30 turns of
        # opus-4-7 tool calls is ~$0.30-0.60 AND ~90-120s — we want this
        # to feel snappy.
        max_wall_seconds=min(settings.architect_max_wall_seconds, 120),
        max_turns=min(settings.architect_max_turns, 8),
        max_cost_usd=min(settings.architect_max_cost_usd, 0.5),
    )
    architect = get_architect()
    log.info(
        "clarify.invoking_architect",
        external_id=external_id,
        provider=settings.architect_provider,
        model=settings.architect_model,
        max_wall_seconds=req.max_wall_seconds,
    )
    result = await architect.clarify_epic(req)
    log.info(
        "clarify.architect_returned",
        external_id=external_id,
        success=result.success,
        num_questions=len(result.questions),
        duration_ms=result.duration_ms,
        errors=result.errors,
    )
    if not result.raw_md and result.errors:
        raise HTTPException(
            status_code=500,
            detail=f"architect produced no output: {'; '.join(result.errors)}",
        )
    return ClarifyDraftOut(
        raw_md=result.raw_md,
        questions=[
            ClarificationQuestionOut(text=q.text, why=q.why) for q in result.questions
        ],
        prompt_version=result.prompt_version,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        errors=result.errors,
    )


@router.get(
    "/tickets/{external_id}/clarification",
    response_model=PersistedClarificationOut | None,
)
async def get_clarification(external_id: str) -> PersistedClarificationOut | None:
    """The latest persisted clarification for this epic, if any.

    Returns null if no clarification has been kicked off (e.g. an epic
    created before auto-clarify was wired in). UI polls this while
    `status == "running"` to see questions stream in.
    """
    ticket, _ = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400, detail=f"clarification is for epics (got kind={ticket.kind})"
        )
    row = await clarification_service.get_latest_for_ticket(ticket.id)
    if row is None:
        return None
    return _clarification_to_out(row)


@router.post(
    "/tickets/{external_id}/clarification",
    response_model=PersistedClarificationOut,
    status_code=201,
)
async def kick_clarification(external_id: str) -> PersistedClarificationOut:
    """Kick off a new clarification for this epic (replacing the visible one).

    Used by the "re-clarify" button on the epic page when the user wants
    fresh questions. The previous clarification rows stay in the DB for
    audit but only the latest is shown.
    """
    ticket, _ = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400, detail=f"clarification is for epics (got kind={ticket.kind})"
        )
    clar_id = await clarification_service.kickoff_clarification(ticket.id)
    async with session_scope() as session:
        row = await session.get(Clarification, clar_id)
        if row is None:
            raise HTTPException(status_code=500, detail="clarification row not found after kickoff")
        session.expunge(row)
    return _clarification_to_out(row)


def _clarification_to_out(row: Clarification) -> PersistedClarificationOut:
    return PersistedClarificationOut(
        id=row.id,
        ticket_id=row.ticket_id,
        status=str(row.status),
        raw_md=row.raw_md or "",
        questions=[
            ClarificationQuestionOut(text=q.get("text", ""), why=q.get("why", ""))
            for q in (row.questions or [])
        ],
        prompt_version=row.prompt_version,
        num_turns=row.num_turns,
        duration_ms=row.duration_ms,
        total_cost_usd=row.total_cost_usd,
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
    )


@router.get("/tickets/{external_id}/clarify/stream")
async def clarify_stream(external_id: str):
    """SSE: streams architect text chunks live during a clarify call, then
    emits a final `done` event with the parsed structured questions.

    Same inputs as `POST /clarify`; same final payload shape (`ClarifyDraftOut`),
    just delivered progressively. The UI can show questions as the architect
    types instead of waiting for the full response.

    Events:
      - `text`  { delta: string }       — incremental chunk of raw markdown
      - `done`  ClarifyDraftOut         — final parsed result; stream closes
      - `error` { detail: string }      — fatal; stream closes
    """
    ticket, repo = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400,
            detail=f"only epics can be clarified (got kind={ticket.kind})",
        )

    repo_root = Path(repo.local_path)
    try:
        registry = DomainRegistry.load(repo_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    available = [
        DomainBrief(name=d.name, description=d.description, paths=list(d.paths))
        for d in registry.domains
    ]
    context_files: list[str] = []
    for d in registry.domains:
        context_files.extend(d.context_files)

    settings = get_settings()
    req = ClarifyRequest(
        repo_path=repo.local_path,
        repo_name=repo.name,
        epic_title=ticket.title,
        epic_body=ticket.body or "",
        available_domains=available,
        default_domain=ticket.domain_name,
        domain_context_files=context_files,
        max_wall_seconds=settings.architect_max_wall_seconds,
        max_turns=settings.architect_max_turns,
        max_cost_usd=min(settings.architect_max_cost_usd, 0.5),
    )

    queue: asyncio.Queue = asyncio.Queue()

    async def on_text(delta: str) -> None:
        await queue.put(("text", delta))

    async def run_clarify() -> None:
        try:
            result = await get_architect().clarify_epic(req, on_text=on_text)
            await queue.put(("done", result))
        except Exception as e:
            log.error("clarify_stream.fatal", external_id=external_id, error=str(e))
            await queue.put(("error", f"{type(e).__name__}: {e}"))

    task = asyncio.create_task(run_clarify())

    async def event_gen():
        import json as _json

        try:
            while True:
                kind, payload = await queue.get()
                if kind == "text":
                    yield {"event": "text", "data": _json.dumps({"delta": payload})}
                elif kind == "done":
                    out = ClarifyDraftOut(
                        raw_md=payload.raw_md,
                        questions=[
                            ClarificationQuestionOut(text=q.text, why=q.why)
                            for q in payload.questions
                        ],
                        prompt_version=payload.prompt_version,
                        num_turns=payload.num_turns,
                        duration_ms=payload.duration_ms,
                        total_cost_usd=payload.total_cost_usd,
                        errors=payload.errors,
                    )
                    yield {"event": "done", "data": out.model_dump_json()}
                    return
                elif kind == "error":
                    yield {"event": "error", "data": _json.dumps({"detail": payload})}
                    return
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_gen())


@router.post("/tickets/{external_id}/decompose/draft", response_model=DecomposeDraftOut)
async def decompose_draft(
    external_id: str, body: DecomposeDraftRequest | None = None
) -> DecomposeDraftOut:
    """Architect proposes a feature/task tree for an epic.

    Does NOT persist anything; the UI shows the result, lets the user edit
    the YAML, and posts to .../decompose/approve.
    """
    ticket, repo = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400,
            detail=f"only epics can be decomposed (got kind={ticket.kind})",
        )

    repo_root = Path(repo.local_path)
    try:
        registry = DomainRegistry.load(repo_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    available = [
        DomainBrief(name=d.name, description=d.description, paths=list(d.paths))
        for d in registry.domains
    ]
    # Pull union of context files across all domains so the architect sees
    # the curated context for whichever feature it scopes to a given domain.
    context_files: list[str] = []
    for d in registry.domains:
        context_files.extend(d.context_files)

    clarifications = (
        [
            ClarificationQA(question=qa.question, answer=qa.answer, why=qa.why)
            for qa in (body.clarifications if body else [])
        ]
        if body
        else []
    )

    settings = get_settings()
    req = DecomposeRequest(
        repo_path=repo.local_path,
        repo_name=repo.name,
        epic_title=ticket.title,
        epic_body=ticket.body or "",
        available_domains=available,
        default_domain=ticket.domain_name,
        domain_context_files=context_files,
        clarifications=clarifications,
        max_wall_seconds=max(settings.architect_max_wall_seconds, 600),
        max_turns=settings.architect_max_turns,
        max_cost_usd=max(settings.architect_max_cost_usd, 2.0),
    )
    result = await get_architect().decompose_epic(req)
    if not result.raw_md:
        raise HTTPException(
            status_code=500,
            detail=f"architect produced no output: {'; '.join(result.errors) or 'unknown'}",
        )
    return DecomposeDraftOut(
        raw_md=result.raw_md,
        features=[
            DecomposedFeatureOut(
                title=f.title,
                description=f.description,
                domain=f.domain,
                depends_on=list(f.depends_on),
                tasks=[
                    DecomposedTaskOut(title=t.title, description=t.description)
                    for t in f.tasks
                ],
            )
            for f in result.features
        ],
        prompt_version=result.prompt_version,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        total_cost_usd=result.total_cost_usd,
        errors=result.errors,
    )


@router.post("/tickets/{external_id}/decompose/approve", response_model=DecomposeApproveOut)
async def decompose_approve(
    external_id: str, req: DecomposeApproveRequest
) -> DecomposeApproveOut:
    """Materialize feature + task rows from the user-approved YAML.

    Lazy: workflows are NOT started for the new tasks. Each task waits for
    the user to click "start workflow" on its page. This avoids a flood of
    concurrent waiting_for_plan workflows for big epics.
    """
    ticket, repo = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400,
            detail=f"only epics can be decomposed (got kind={ticket.kind})",
        )

    features, parse_errors = parse_decomposition(req.raw_md)
    if parse_errors:
        raise HTTPException(
            status_code=400,
            detail="; ".join(parse_errors),
        )
    if not features:
        raise HTTPException(status_code=400, detail="no features parsed from YAML")

    # Validate domains up front so a parse pass doesn't leave half-created rows.
    repo_root = Path(repo.local_path)
    try:
        registry = DomainRegistry.load(repo_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    valid_domains = {d.name for d in registry.domains}

    epic_default_domain = ticket.domain_name

    feature_ext_ids: list[str] = []
    task_ext_ids: list[str] = []
    # title → feature row id, for resolving depends_on after all features are created.
    title_to_feature_id: dict[str, int] = {}

    async with session_scope() as session:
        for f in features:
            f_domain = f.domain or epic_default_domain
            if f_domain and f_domain not in valid_domains:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown domain {f_domain!r} on feature {f.title!r}",
                )
            f_ext_id = f"f-{uuid.uuid4().hex[:8]}"
            f_row = Ticket(
                repo_id=repo.id,
                external_id=f_ext_id,
                title=f.title,
                body=f.description,
                domain_name=f_domain,
                status=TicketStatus.pending,
                kind=TicketKind.feature,
                parent_id=ticket.id,
            )
            session.add(f_row)
            await session.flush()
            feature_ext_ids.append(f_ext_id)
            title_to_feature_id[f.title] = f_row.id

            for t in f.tasks:
                t_ext_id = f"t-{uuid.uuid4().hex[:8]}"
                t_row = Ticket(
                    repo_id=repo.id,
                    external_id=t_ext_id,
                    title=t.title,
                    body=t.description,
                    domain_name=f_domain,
                    status=TicketStatus.pending,
                    kind=TicketKind.task,
                    parent_id=f_row.id,
                )
                session.add(t_row)
                await session.flush()
                task_ext_ids.append(t_ext_id)

        # Materialize feature dependencies. The parser already rejected cycles
        # and unknown titles, but defend against duplicates / self-loops at
        # the DB layer just in case.
        for f in features:
            dependent_id = title_to_feature_id.get(f.title)
            if dependent_id is None:
                continue
            for dep_title in f.depends_on:
                prereq_id = title_to_feature_id.get(dep_title)
                if prereq_id is None or prereq_id == dependent_id:
                    continue
                session.add(
                    FeatureDependency(
                        dependent_id=dependent_id, prerequisite_id=prereq_id
                    )
                )

    log.info(
        "epic.decomposed",
        external_id=external_id,
        feature_count=len(feature_ext_ids),
        task_count=len(task_ext_ids),
        approver=req.approver,
    )
    return DecomposeApproveOut(
        feature_external_ids=feature_ext_ids,
        task_external_ids=task_ext_ids,
    )


# ---- Roadmap + dependency CRUD --------------------------------------------


async def _compute_waves(
    session, epic_id: int
) -> tuple[list[list[Ticket]], list[Ticket], dict[int, set[int]]]:
    """Group an epic's features into topological waves.

    Returns (waves, cyclic, prereqs_by_id) where:
      - waves: ordered list of lists; each inner list is a wave whose
        features can be worked on in parallel.
      - cyclic: features that couldn't be placed (involved in a cycle).
      - prereqs_by_id: id -> set of prerequisite feature ids (so callers can
        emit the structured edges for the UI).
    """
    rows = (
        await session.execute(
            select(Ticket).where(
                Ticket.parent_id == epic_id,
                Ticket.kind == TicketKind.feature.value,
            )
        )
    ).scalars().all()
    feature_ids = {f.id for f in rows}
    if not feature_ids:
        return [], [], {}

    deps = (
        await session.execute(
            select(FeatureDependency).where(
                FeatureDependency.dependent_id.in_(feature_ids),
                FeatureDependency.prerequisite_id.in_(feature_ids),
            )
        )
    ).scalars().all()

    prereqs: dict[int, set[int]] = {fid: set() for fid in feature_ids}
    for d in deps:
        prereqs[d.dependent_id].add(d.prerequisite_id)

    waves: list[list[Ticket]] = []
    placed: set[int] = set()
    remaining = list(rows)
    while remaining:
        wave = [
            f for f in remaining if prereqs[f.id].issubset(placed)
        ]
        if not wave:
            # Cycle — surface remainder so the UI can flag it.
            return waves, remaining, prereqs
        # Stable order within a wave: by id ascending so successive renders
        # don't reshuffle.
        wave.sort(key=lambda t: t.id)
        waves.append(wave)
        placed.update(f.id for f in wave)
        remaining = [f for f in remaining if f.id not in placed]
    return waves, [], prereqs


@router.get("/tickets/{external_id}/roadmap", response_model=RoadmapOut)
async def get_roadmap(external_id: str) -> RoadmapOut:
    """Topological view of an epic's features.

    Wave 0 = no prerequisites. Wave N = prereqs are all in waves < N.
    Features in the same wave can be worked on in parallel.
    """
    epic, _ = await _get_ticket_and_repo(external_id)
    if str(epic.kind) != TicketKind.epic.value:
        raise HTTPException(
            status_code=400,
            detail=f"roadmap is for epics (got kind={epic.kind})",
        )

    async with session_scope() as session:
        waves, cyclic, prereqs = await _compute_waves(session, epic.id)
        # Resolve prereq ids to external_ids for the UI.
        all_ids = {f.id for w in waves for f in w} | {f.id for f in cyclic}
        id_to_ext = (
            dict(
                (
                    await session.execute(
                        select(Ticket.id, Ticket.external_id).where(
                            Ticket.id.in_(all_ids)
                        )
                    )
                ).all()
            )
            if all_ids
            else {}
        )
        # Child counts (= task count for each feature) in one query.
        from sqlalchemy import func as _func

        if all_ids:
            counts = dict(
                (
                    await session.execute(
                        select(Ticket.parent_id, _func.count(Ticket.id))
                        .where(Ticket.parent_id.in_(all_ids))
                        .group_by(Ticket.parent_id)
                    )
                ).all()
            )
        else:
            counts = {}

    def _to_out(t: Ticket) -> RoadmapFeatureOut:
        return RoadmapFeatureOut(
            id=t.id,
            external_id=t.external_id,
            title=t.title,
            status=str(t.status),
            domain_name=t.domain_name,
            workflow_id=t.workflow_id,
            child_count=int(counts.get(t.id, 0) or 0),
            prerequisite_external_ids=sorted(
                id_to_ext[p] for p in prereqs.get(t.id, set()) if p in id_to_ext
            ),
        )

    return RoadmapOut(
        epic_external_id=external_id,
        waves=[
            RoadmapWaveOut(index=i, features=[_to_out(f) for f in wave])
            for i, wave in enumerate(waves)
        ],
        cyclic_external_ids=[t.external_id for t in cyclic],
    )


@router.post("/tickets/{external_id}/dependencies", status_code=201)
async def add_dependency(external_id: str, req: AddDependencyRequest) -> dict:
    """Make `external_id` depend on `req.prerequisite_external_id`. Both
    must be features under the same epic. Cycles are rejected."""
    dependent, _ = await _get_ticket_and_repo(external_id)
    prereq, _ = await _get_ticket_and_repo(req.prerequisite_external_id)

    if str(dependent.kind) != TicketKind.feature.value or str(
        prereq.kind
    ) != TicketKind.feature.value:
        raise HTTPException(
            status_code=400,
            detail="both endpoints must be features",
        )
    if dependent.id == prereq.id:
        raise HTTPException(status_code=400, detail="a feature can't depend on itself")
    if (
        dependent.parent_id is None
        or prereq.parent_id is None
        or dependent.parent_id != prereq.parent_id
    ):
        raise HTTPException(
            status_code=400,
            detail="features must share the same epic to declare a dependency",
        )

    async with session_scope() as session:
        # Reject duplicates cleanly (the unique constraint would 500).
        already = (
            await session.execute(
                select(FeatureDependency.id).where(
                    FeatureDependency.dependent_id == dependent.id,
                    FeatureDependency.prerequisite_id == prereq.id,
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            return {"id": int(already), "created": False}

        # Cycle check: would adding this edge create a cycle? Temporarily
        # extend prereqs, recompute waves, see if any feature is unplaced.
        waves, cyclic, _ = await _compute_waves(session, dependent.parent_id)
        # Simulate the new edge in memory.
        prereqs_now: dict[int, set[int]] = {
            f.id: set() for w in waves for f in w
        } | {f.id: set() for f in cyclic}
        # Re-derive from DB (cheap; <=N rows).
        existing = (
            await session.execute(
                select(FeatureDependency).where(
                    FeatureDependency.dependent_id.in_(prereqs_now.keys()),
                    FeatureDependency.prerequisite_id.in_(prereqs_now.keys()),
                )
            )
        ).scalars().all()
        for d in existing:
            prereqs_now.setdefault(d.dependent_id, set()).add(d.prerequisite_id)
        prereqs_now.setdefault(dependent.id, set()).add(prereq.id)
        # Topo sort
        placed: set[int] = set()
        remaining = set(prereqs_now)
        while remaining:
            next_set = {
                fid for fid in remaining if prereqs_now[fid].issubset(placed)
            }
            if not next_set:
                raise HTTPException(
                    status_code=400, detail="adding this dependency would create a cycle"
                )
            placed |= next_set
            remaining -= next_set

        row = FeatureDependency(
            dependent_id=dependent.id, prerequisite_id=prereq.id
        )
        session.add(row)
        await session.flush()
        return {"id": row.id, "created": True}


@router.delete(
    "/tickets/{external_id}/dependencies/{prereq_external_id}", status_code=204
)
async def delete_dependency(external_id: str, prereq_external_id: str) -> None:
    """Remove the edge `external_id` → `prereq_external_id`."""
    dependent, _ = await _get_ticket_and_repo(external_id)
    prereq, _ = await _get_ticket_and_repo(prereq_external_id)
    async with session_scope() as session:
        row = (
            await session.execute(
                select(FeatureDependency).where(
                    FeatureDependency.dependent_id == dependent.id,
                    FeatureDependency.prerequisite_id == prereq.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="dependency not found")
        await session.delete(row)


@router.post("/tickets/{external_id}/start-workflow", response_model=CreateTicketResult)
async def start_workflow(
    external_id: str, base_ref: str = "main"
) -> CreateTicketResult:
    """Launch a FeatureWorkflow for an existing task ticket.

    Used by tasks materialized through epic decomposition — they exist in
    the DB but haven't kicked off their workflow yet. Idempotent against the
    ALLOW_DUPLICATE_FAILED_ONLY reuse policy.
    """
    ticket, repo = await _get_ticket_and_repo(external_id)
    if str(ticket.kind) != TicketKind.task.value:
        raise HTTPException(
            status_code=400,
            detail=f"only tasks have workflows (got kind={ticket.kind})",
        )
    if not ticket.domain_name:
        raise HTTPException(
            status_code=400,
            detail="task has no domain — set one before starting the workflow",
        )

    repo_root = Path(repo.local_path)
    try:
        registry = DomainRegistry.load(repo_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        chosen = registry.get(ticket.domain_name)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    settings = get_settings()
    client = await get_temporal_client()
    inp = FeatureWorkflowInput(
        ticket_id=ticket.id,
        domain_name=chosen.name,
        domain_description=chosen.description,
        domain_paths=list(chosen.paths),
        base_ref=base_ref,
        llm_task_queue=settings.temporal_task_queue_llm,
        cleanup_worktree=False,
    )
    workflow_id = feature_workflow_id(repo_root, external_id)
    try:
        await client.start_workflow(
            FeatureWorkflow.run,
            inp,
            id=workflow_id,
            task_queue=settings.temporal_task_queue_features,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            search_attributes=TypedSearchAttributes(
                [
                    SearchAttributePair(REPO_NAME, repo_slug(repo_root)),
                    SearchAttributePair(DOMAIN, chosen.name),
                    SearchAttributePair(TICKET_ID, external_id),
                    SearchAttributePair(PRAVI_STATUS, "waiting_for_plan"),
                ]
            ),
        )
    except RPCError as e:
        if "WorkflowExecutionAlreadyStarted" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"workflow {workflow_id} is already running",
            ) from e
        raise

    web_url = f"{settings.web_url_base.rstrip('/')}/tickets/{external_id}"
    return CreateTicketResult(
        external_id=external_id,
        ticket_id=ticket.id,
        workflow_id=workflow_id,
        web_url=web_url,
    )


async def _resolve_roots(
    session, external_ids: list[str]
) -> tuple[list[Ticket], list[str]]:
    """Resolve `external_ids` to Ticket rows and prune descendants of other
    selected roots. Returns (root_tickets, not_found_external_ids)."""
    rows = (
        await session.execute(
            select(Ticket).where(Ticket.external_id.in_(external_ids))
        )
    ).scalars().all()
    found_ext_ids = {r.external_id for r in rows}
    not_found = [ext for ext in external_ids if ext not in found_ext_ids]

    selected = {r.id for r in rows}
    roots: list[Ticket] = []
    for r in rows:
        cur = r.parent_id
        is_descendant = False
        while cur is not None:
            if cur in selected:
                is_descendant = True
                break
            parent = await session.get(Ticket, cur)
            cur = parent.parent_id if parent else None
        if not is_descendant:
            roots.append(r)
    return roots, not_found


async def _collect_subtree_ids(session, root_ids: list[int]) -> set[int]:
    all_ids: set[int] = set(root_ids)
    frontier = list(root_ids)
    while frontier:
        child_ids = (
            await session.execute(
                select(Ticket.id).where(Ticket.parent_id.in_(frontier))
            )
        ).scalars().all()
        new = [c for c in child_ids if c not in all_ids]
        all_ids.update(new)
        frontier = new
    return all_ids


async def _terminate_workflows(workflow_ids: list[str], reason: str) -> int:
    """Best-effort: terminate each workflow. Closed/missing ones log + skip."""
    if not workflow_ids:
        return 0
    terminated = 0
    try:
        client = await get_temporal_client()
    except Exception as e:
        log.warning("delete.workflow_client_failed", error=str(e))
        return 0
    for wf_id in workflow_ids:
        try:
            await client.get_workflow_handle(wf_id).terminate(reason=reason)
            terminated += 1
        except RPCError as e:
            log.info(
                "delete.workflow_terminate_skipped",
                workflow_id=wf_id,
                reason=str(e),
            )
    return terminated


async def _hard_delete_subtree(
    external_ids: list[str], *, reason: str
) -> BulkDeleteResult:
    """Shared helper for single + bulk delete.

    Steps: resolve roots → collect subtree IDs → terminate workflows →
    cascade-delete dependent rows + tickets in FK-safe order.
    """
    from sqlalchemy import delete as sa_delete

    async with session_scope() as session:
        roots, not_found = await _resolve_roots(session, external_ids)
        if not roots and not_found:
            raise HTTPException(
                status_code=404,
                detail=f"none of the requested tickets exist: {not_found}",
            )
        root_ids = [r.id for r in roots]
        root_ext_ids = [r.external_id for r in roots]
        all_ids = await _collect_subtree_ids(session, root_ids)
        wf_ids = (
            await session.execute(
                select(Ticket.workflow_id).where(
                    Ticket.id.in_(all_ids),
                    Ticket.workflow_id.is_not(None),
                )
            )
        ).scalars().all()

    # Terminate workflows OUTSIDE the DB transaction so a Temporal hiccup
    # doesn't leave the DB in a half-deleted state.
    terminated = await _terminate_workflows(list(wf_ids), reason=reason)

    async with session_scope() as session:
        await session.execute(sa_delete(Event).where(Event.ticket_id.in_(all_ids)))
        await session.execute(sa_delete(Run).where(Run.ticket_id.in_(all_ids)))
        await session.execute(sa_delete(Plan).where(Plan.ticket_id.in_(all_ids)))
        await session.execute(
            sa_delete(Clarification).where(Clarification.ticket_id.in_(all_ids))
        )
        # FeatureDependency cascades via FK; tickets last.
        await session.execute(sa_delete(Ticket).where(Ticket.id.in_(all_ids)))

    log.info(
        "ticket.deleted",
        roots=root_ext_ids,
        total=len(all_ids),
        workflows_terminated=terminated,
    )
    return BulkDeleteResult(
        deleted_root_external_ids=root_ext_ids,
        not_found_external_ids=not_found,
        deleted_ticket_count=len(all_ids),
        workflows_terminated=terminated,
    )


@router.delete("/tickets/{external_id}", status_code=200)
async def delete_ticket(external_id: str) -> dict:
    """Delete a ticket plus its full descendant subtree. Hard-delete."""
    result = await _hard_delete_subtree(
        [external_id], reason=f"ticket {external_id} deleted"
    )
    return {
        "deleted_ticket_count": result.deleted_ticket_count,
        "workflows_terminated": result.workflows_terminated,
    }


@router.post("/tickets/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete_tickets(req: BulkDeleteRequest) -> BulkDeleteResult:
    """Delete multiple tickets atomically (each plus its subtree).

    If the selection includes both an epic and one of its descendants,
    the descendant is dropped from the work list (it'd be cascade-deleted
    by the epic anyway). Result reports both what was processed and any
    requested IDs that didn't resolve.
    """
    if not req.external_ids:
        raise HTTPException(status_code=400, detail="external_ids is empty")
    return await _hard_delete_subtree(
        req.external_ids, reason="bulk-deleted from UI"
    )


@router.post("/tickets/{external_id}/cancel")
async def cancel(external_id: str) -> dict:
    _, repo = await _get_ticket_and_repo(external_id)
    client = await get_temporal_client()
    workflow_id = feature_workflow_id(Path(repo.local_path), external_id)
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(FeatureWorkflow.cancel)
    except RPCError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"signalled": True, "workflow_id": workflow_id}


@router.get("/runs", response_model=list[RunOut])
async def list_runs(limit: int = 100) -> list[RunOut]:
    """Most-recent agent runs across all tickets — powers the /runs dashboard.

    Joins Run → Ticket → Repo and left-joins the matching `run_finished`
    event to pull final cost/turns/duration from its payload. In-flight runs
    have null metrics; the UI shows a "running" pill instead.
    """
    async with session_scope() as session:
        stmt = (
            select(Run, Ticket, Repo, Event)
            .join(Ticket, Run.ticket_id == Ticket.id)
            .join(Repo, Ticket.repo_id == Repo.id)
            .outerjoin(
                Event,
                and_(Event.run_id == Run.id, Event.kind == KIND_RUN_FINISHED),
            )
            .order_by(Run.id.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [_run_to_out(r, t, rp, e) for r, t, rp, e in rows]


def _run_to_out(run: Run, ticket: Ticket, repo: Repo, finish: Event | None) -> RunOut:
    payload = (finish.payload if finish else None) or {}
    return RunOut(
        id=run.id,
        ticket_id=ticket.id,
        ticket_external_id=ticket.external_id,
        ticket_title=ticket.title,
        repo_name=repo.name,
        kind=str(run.kind),
        status=str(run.status),
        started_at=run.started_at,
        ended_at=run.ended_at,
        error=run.error,
        num_turns=payload.get("num_turns"),
        duration_ms=payload.get("duration_ms"),
        total_cost_usd=payload.get("total_cost_usd"),
    )


@router.get("/tickets/{external_id}/run/stream")
async def run_stream(external_id: str):
    """SSE: live timeline of the most recent dev agent run.

    Replays existing events for the latest Run row, then streams new events
    as they arrive via Postgres LISTEN/NOTIFY. Closes when a `run_finished`
    sentinel event is seen.

    A client reconnecting mid-run gets a full replay first, so refreshing
    the page never blanks out. If no run has started yet, the stream stays
    open and waits — the first event will arrive when dev_activity boots.
    """
    ticket, _ = await _get_ticket_and_repo(external_id)
    ticket_id = ticket.id  # capture before session detach

    async def event_gen():
        # LISTEN must be established before the replay query — otherwise an
        # event emitted in that window would be lost. Inside the `async with`
        # the connection is already subscribed.
        async with listen_events(ticket_id) as queue:
            # Find the most recent run for this ticket; replay its events.
            async with session_scope() as session:
                latest_run_id = (
                    await session.execute(
                        select(Run.id)
                        .where(Run.ticket_id == ticket_id)
                        .order_by(Run.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

                replay: list[Event] = []
                if latest_run_id is not None:
                    rows = await session.execute(
                        select(Event)
                        .where(
                            Event.ticket_id == ticket_id,
                            Event.run_id == latest_run_id,
                        )
                        .order_by(Event.id.asc())
                    )
                    replay = list(rows.scalars().all())
                    for r in replay:
                        session.expunge(r)

            last_yielded_id = 0
            saw_finished = False
            for evt in replay:
                last_yielded_id = max(last_yielded_id, evt.id)
                yield {
                    "event": "run",
                    "data": _event_to_out(evt).model_dump_json(),
                }
                if evt.kind == KIND_RUN_FINISHED:
                    saw_finished = True

            if saw_finished:
                yield {"event": "close", "data": "{}"}
                return

            # Live phase — IDs trickle in via the LISTEN queue.
            while True:
                event_id = await queue.get()
                if event_id <= last_yielded_id:
                    continue  # already replayed
                async with session_scope() as session:
                    evt = await session.get(Event, event_id)
                    if evt is None:
                        continue
                    session.expunge(evt)
                last_yielded_id = evt.id
                yield {
                    "event": "run",
                    "data": _event_to_out(evt).model_dump_json(),
                }
                if evt.kind == KIND_RUN_FINISHED:
                    yield {"event": "close", "data": "{}"}
                    return

    return EventSourceResponse(event_gen())


def _event_to_out(evt: Event) -> RunEventOut:
    return RunEventOut(
        id=evt.id,
        ticket_id=evt.ticket_id,
        run_id=evt.run_id,
        kind=evt.kind,
        message=evt.message,
        payload=evt.payload,
        at=evt.at,
    )


@router.get("/tickets/{external_id}/status/stream")
async def status_stream(external_id: str):
    """SSE: emits a WorkflowStatusEvent each time the workflow's status changes,
    and closes once the workflow reaches a terminal state."""
    _, repo = await _get_ticket_and_repo(external_id)
    workflow_id = feature_workflow_id(Path(repo.local_path), external_id)

    async def event_gen():
        client = await get_temporal_client()
        last_payload: tuple | None = None
        terminal_status_ints = {2, 3, 4, 5, 7}  # COMPLETED/FAILED/CANCELED/TERMINATED/TIMED_OUT
        execution_status_names = {
            1: "RUNNING",
            2: "COMPLETED",
            3: "FAILED",
            4: "CANCELED",
            5: "TERMINATED",
            6: "CONTINUED_AS_NEW",
            7: "TIMED_OUT",
        }

        while True:
            try:
                handle = client.get_workflow_handle(workflow_id)
                desc = await handle.describe()
                status_int = int(desc.status)
                exec_status = execution_status_names.get(status_int, str(status_int))

                wf_status = ""
                plan_id: int | None = None
                if status_int == 1:  # RUNNING
                    try:
                        wf_status = await handle.query(FeatureWorkflow.current_status)
                        plan_id = await handle.query(FeatureWorkflow.plan_id)
                    except RPCError:
                        wf_status = "unknown"
                else:
                    wf_status = "done"

                payload = (wf_status, exec_status, plan_id)
                if payload != last_payload:
                    evt = WorkflowStatusEvent(
                        workflow_id=workflow_id,
                        status=wf_status,
                        execution_status=exec_status,
                        plan_id=plan_id,
                        at=datetime.now(UTC),
                    )
                    yield {"event": "status", "data": evt.model_dump_json()}
                    last_payload = payload

                if status_int in terminal_status_ints:
                    yield {"event": "close", "data": "{}"}
                    return
            except RPCError as e:
                # If the workflow no longer exists, end the stream cleanly.
                yield {"event": "error", "data": str(e)}
                return
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())
