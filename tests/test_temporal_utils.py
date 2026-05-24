from __future__ import annotations

from pathlib import Path

from pravi.temporal_utils import feature_workflow_id, repo_slug, slugify


def test_slugify_basic() -> None:
    assert slugify("Blissful Infra") == "blissful-infra"
    assert slugify("HELLO_world!! 123") == "hello-world-123"
    assert slugify("---trim---") == "trim"
    assert slugify("") == ""


def test_repo_slug_from_path() -> None:
    assert repo_slug("/Users/cavanpage/repos/blissful-infra") == "blissful-infra"
    assert repo_slug(Path("/tmp/Some_Project")) == "some-project"


def test_repo_slug_empty_fallback(tmp_path: Path) -> None:
    # Path("/") has empty .name; ensure we fall back rather than producing
    # a malformed workflow ID.
    assert repo_slug(Path("/")) == "repo"


def test_feature_workflow_id() -> None:
    wid = feature_workflow_id("/Users/cavanpage/repos/blissful-infra", "123")
    assert wid == "feature-blissful-infra-123"


def test_feature_workflow_id_slugifies_ticket() -> None:
    # Ticket IDs should be slugified too — protects against weird external IDs.
    wid = feature_workflow_id("/tmp/repo", "ABC #99")
    assert wid == "feature-repo-abc-99"
