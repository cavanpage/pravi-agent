"""Pydantic models that go over the wire. Kept separate from db.models on
purpose — the DB schema is internal, the API shape is a contract."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RepoOut(BaseModel):
    id: int
    name: str
    local_path: str
    github_owner: str | None = None
    github_name: str | None = None


class GitHubRepoRef(BaseModel):
    """Coordinates from the GitHub search picker.

    When set on a create-ticket request and `repo_path` is empty, the server
    clones the repo (idempotent, into clone_base) and creates / reuses the
    Repo row before creating the ticket. Lets the user pick a repo from
    GitHub without typing a local path.
    """

    owner: str
    name: str
    clone_url: str
    default_branch: str = "main"


class CreateTicketRequest(BaseModel):
    external_id: str | None = None
    title: str
    body: str = ""
    # Required for epics + standalone tasks unless `github_repo` is provided
    # (in which case the server lazily clones + registers it). For features
    # and parented tasks, `parent_external_id` inherits the repo.
    repo_path: str | None = None
    github_repo: GitHubRepoRef | None = None
    domain_name: str | None = None
    base_ref: str = "main"
    cleanup_worktree: bool = False
    # Hierarchy. Default "task" preserves existing behavior.
    kind: str = "task"
    # External ID of the parent ticket (epic for features, feature for tasks).
    parent_external_id: str | None = None
    # Optional cumulative spend cap (USD). Null inherits from ancestor / env.
    cost_ceiling_usd: float | None = None


class CreateTicketResult(BaseModel):
    external_id: str
    ticket_id: int
    workflow_id: str | None  # None for epics/features (no workflow today)
    web_url: str


class TicketOut(BaseModel):
    id: int
    external_id: str
    title: str
    body: str
    domain_name: str | None
    status: str
    workflow_id: str | None
    repo: RepoOut
    kind: str
    parent_external_id: str | None
    # Counts of immediate children — useful for epic/feature dashboards.
    # Cheap to compute and lets the UI skip a second request.
    child_count: int = 0
    # Used by the home dashboard to sort by recency / age.
    created_at: datetime
    updated_at: datetime
    # GitHub PR — set after the push+PR activity runs successfully.
    pr_number: int | None = None
    pr_url: str | None = None
    # Per-ticket cumulative spend cap (USD). Null = inherit from parent /
    # env default / unlimited. See /tickets/{id}/cost-rollup for the
    # effective value after walking the chain.
    cost_ceiling_usd: float | None = None


class TicketBudgetUpdate(BaseModel):
    """PATCH body for `/api/tickets/{external_id}/budget`.

    Pass null to clear (revert to inheritance). Cannot be negative.
    """

    cost_ceiling_usd: float | None


class BudgetBreakdownOut(BaseModel):
    ticket_id: int
    external_id: str
    kind: str
    title: str
    own_ceiling_usd: float | None
    spent_usd: float
    remaining_usd: float | None


class CostRollupOut(BaseModel):
    """Response for `/api/tickets/{external_id}/cost-rollup`.

    `chain` is self first, then each ancestor in order. UI can show which
    level is currently the binding constraint via `constraint_source`.
    """

    ticket_id: int
    external_id: str
    kind: str
    own_ceiling_usd: float | None
    own_spent_usd: float
    effective_remaining_usd: float | None
    constraint_source: str  # self | feature | epic | env_default | unlimited
    chain: list[BudgetBreakdownOut]


class DomainOut(BaseModel):
    name: str
    description: str
    paths: list[str]
    test: str | None
    build: str | None


class PlanDraftRequest(BaseModel):
    # Optional override — defaults to ticket.domain_name from DB.
    domain_name: str | None = None
    # Override path to domains.yaml (for repos that don't have it inside themselves yet).
    domains_file: str | None = None


class PlanDraftOut(BaseModel):
    plan_md: str
    prompt_version: str
    num_turns: int
    duration_ms: int
    total_cost_usd: float | None
    domain_name: str


class PlanApproveRequest(BaseModel):
    content_md: str
    domain_name: str
    approver: str | None = None
    domains_file: str | None = None  # snapshot source


class PlanApproveOut(BaseModel):
    plan_id: int
    signalled: bool
    workflow_id: str


class WorkflowStatusEvent(BaseModel):
    workflow_id: str
    status: str  # one of FeatureWorkflow's STATUS_* constants
    execution_status: str  # Temporal-side: RUNNING / COMPLETED / FAILED / ...
    plan_id: int | None
    at: datetime


class RunOut(BaseModel):
    """One row on the /runs dashboard.

    Combines the Run row (lifecycle) with metrics pulled from its
    `run_finished` event payload (turns + cost from the SDK). For in-flight
    runs those fields are null; the UI shows a "running" pill instead.
    """

    id: int
    ticket_id: int
    ticket_external_id: str
    ticket_title: str
    repo_name: str
    kind: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    error: str | None
    # From run_finished event payload — null while in-flight.
    num_turns: int | None
    duration_ms: int | None
    total_cost_usd: float | None


class ClarificationQuestionOut(BaseModel):
    text: str
    why: str = ""


class PersistedClarificationOut(BaseModel):
    """Latest clarification record for an epic.

    `status` lifecycle: pending → running → done | failed.
    `raw_md` updates progressively while running so the UI can partial-parse
    questions; on `done`, `questions` is the canonical list.
    """

    id: int
    ticket_id: int
    status: str
    raw_md: str
    questions: list[ClarificationQuestionOut]
    prompt_version: str | None
    num_turns: int | None
    duration_ms: int | None
    total_cost_usd: float | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class ClarifyDraftOut(BaseModel):
    """Architect's pre-decomposition clarifying questions.

    `questions=[]` with no errors means the architect chose not to ask — the
    UI should let the user proceed straight to decomposition.
    """

    raw_md: str
    questions: list[ClarificationQuestionOut]
    prompt_version: str
    duration_ms: int
    num_turns: int
    total_cost_usd: float | None
    errors: list[str] = []


class ClarificationQAIn(BaseModel):
    question: str
    answer: str = ""
    why: str = ""


class DecomposedTaskOut(BaseModel):
    title: str
    description: str = ""
    # NOTE: feature-level depends_on lives on DecomposedFeatureOut below.


class DecomposedFeatureOut(BaseModel):
    title: str
    description: str = ""
    domain: str | None = None
    tasks: list[DecomposedTaskOut]
    depends_on: list[str] = []  # sibling feature titles


class RoadmapFeatureOut(BaseModel):
    """Feature row enriched with its prerequisite external IDs — drives the
    roadmap view. `external_id` matches the Ticket's external_id."""

    id: int
    external_id: str
    title: str
    status: str
    domain_name: str | None
    workflow_id: str | None
    child_count: int
    prerequisite_external_ids: list[str]


class RoadmapWaveOut(BaseModel):
    """One layer in the topological sort. All features in a wave are
    independent of each other (within the epic) and can be worked on in
    parallel; features in wave N depend only on waves < N."""

    index: int
    features: list[RoadmapFeatureOut]


class RoadmapOut(BaseModel):
    epic_external_id: str
    waves: list[RoadmapWaveOut]
    # Features that couldn't be placed (involved in a cycle). Should normally
    # be empty — surface them so the UI can flag the problem.
    cyclic_external_ids: list[str] = []


class GitHubConnectionOut(BaseModel):
    """Active GitHub connection (or null if user hasn't connected yet)."""

    id: int
    github_user_login: str
    github_user_avatar_url: str | None
    scopes: str | None
    created_at: datetime


class GitHubRepoOut(BaseModel):
    """One repo from `/api/auth/github/repos/search`.

    Mirrors what the new-ticket picker needs: enough to display + enough to
    later clone. `clone_url` is the HTTPS form; a token is injected at clone
    time, never stored at rest on the repo's git remote.
    """

    owner: str
    name: str
    full_name: str
    description: str | None = None
    private: bool = False
    default_branch: str = "main"
    clone_url: str | None = None
    ssh_url: str | None = None
    updated_at: str | None = None


class BulkDeleteRequest(BaseModel):
    """Delete each of these tickets and their full subtrees.

    Server deduplicates: if the selection contains both an epic and one of
    its descendants, the descendant is filtered out (it'd be deleted by the
    epic's cascade anyway). Returns counts + the resolved root external_ids
    so the UI can confirm what actually went.
    """

    external_ids: list[str]


class BulkDeleteResult(BaseModel):
    deleted_root_external_ids: list[str]
    not_found_external_ids: list[str]
    deleted_ticket_count: int
    workflows_terminated: int


class AddDependencyRequest(BaseModel):
    """Add a `dependent_external_id` → `prerequisite_external_id` edge.

    Both must be features under the same epic. Server validates + rejects
    cycles by recomputing the topological sort after the proposed add.
    """

    prerequisite_external_id: str


class DecomposeDraftOut(BaseModel):
    """Architect's proposed feature/task tree for an epic.

    `raw_md` is the full markdown response (so the UI can let the user edit
    it freely). `features` is the parsed structured tree. If parsing failed
    `features` is empty and `errors` explains why — the user can still fix
    the YAML in the editor and re-approve.
    """

    raw_md: str
    features: list[DecomposedFeatureOut]
    prompt_version: str
    num_turns: int
    duration_ms: int
    total_cost_usd: float | None
    errors: list[str] = []


class DecomposeDraftRequest(BaseModel):
    """Optional clarifications gathered from the clarify step."""

    clarifications: list[ClarificationQAIn] = []


class DecomposeApproveRequest(BaseModel):
    """The (possibly edited) markdown the user is approving.

    Server re-parses the YAML block and materializes feature + task rows.
    No workflows are started here; each task workflow boots lazily when the
    user opens the task page and clicks "start workflow".
    """

    raw_md: str
    approver: str | None = None


class DecomposeApproveOut(BaseModel):
    """External IDs of the rows just created — useful so the UI can refresh."""

    feature_external_ids: list[str]
    task_external_ids: list[str]


class RunEventOut(BaseModel):
    """One event from /api/tickets/{id}/run/stream.

    `kind` maps to the runner's transcript entry kinds plus the lifecycle
    sentinels `run_started` / `run_finished`. `payload` is whatever
    structured data the emitter attached (tool input, usage data, etc.).
    """

    id: int
    ticket_id: int
    run_id: int | None
    kind: str
    message: str
    payload: dict | None
    at: datetime
