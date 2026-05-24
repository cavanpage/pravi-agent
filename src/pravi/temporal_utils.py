"""Shared Temporal helpers — search attributes, IDs, slugs."""
from __future__ import annotations

import re
from pathlib import Path

from temporalio.common import SearchAttributeKey

# Custom search attributes registered at the namespace level via
# `scripts/setup-temporal.sh`. Use SearchAttributeKey.for_keyword (exact-match)
# rather than for_text (full-text); we want filterable facets in the UI.
REPO_NAME = SearchAttributeKey.for_keyword("RepoName")
DOMAIN = SearchAttributeKey.for_keyword("Domain")
TICKET_ID = SearchAttributeKey.for_keyword("TicketId")
PRAVI_STATUS = SearchAttributeKey.for_keyword("PraviStatus")

ALL_SEARCH_ATTRIBUTES = [REPO_NAME, DOMAIN, TICKET_ID, PRAVI_STATUS]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, replace non-alphanumeric runs with '-', trim edges."""
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def repo_slug(repo_path: Path | str) -> str:
    """Derive a stable repo slug from its filesystem path basename."""
    name = Path(repo_path).expanduser().resolve().name
    return slugify(name) or "repo"


def feature_workflow_id(repo_path: Path | str, ticket_id: str) -> str:
    """Canonical workflow ID: `feature-<repo-slug>-<ticket-id>`.

    Stable across re-runs of the same ticket — combined with
    WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY this prevents accidental
    double-starts but still lets you retry after a failure.
    """
    return f"feature-{repo_slug(repo_path)}-{slugify(ticket_id)}"
