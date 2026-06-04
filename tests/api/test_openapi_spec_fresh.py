"""The committed OpenAPI spec must match what `create_app().openapi()` emits.

If a contributor adds or changes a route without re-running
`pravi openapi-dump`, the JSON under `docs/api/openapi.json` will drift
from the live FastAPI app and the REST reference in `docs/api/README.md`
will silently lie to API consumers. This test catches that drift in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Resolve relative to this file rather than CWD so the test passes no
# matter where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "docs" / "api" / "openapi.json"

REGEN_HINT = (
    "Committed docs/api/openapi.json is out of date with the FastAPI app. "
    "Re-run `pravi openapi-dump` and commit the result."
)


def test_committed_openapi_spec_file_exists():
    """Guard rail: a missing file is a different failure mode than drift,
    and deserves a clearer message than a JSONDecodeError further down."""
    assert SPEC_PATH.is_file(), (
        f"Expected committed OpenAPI spec at {SPEC_PATH.relative_to(REPO_ROOT)}. "
        "Generate it with `pravi openapi-dump`."
    )


def test_committed_openapi_spec_matches_live_app():
    """Deep-equality between the committed JSON and the freshly generated
    schema from `create_app().openapi()`. We re-serialize through JSON on
    both sides so non-JSON Python types (tuples, sets, …) inside the live
    schema can't produce a spurious mismatch against the on-disk JSON."""
    if not SPEC_PATH.is_file():
        pytest.skip(f"{SPEC_PATH.relative_to(REPO_ROOT)} missing — see sibling test")

    from pravi.api.app import create_app

    live_schema = json.loads(json.dumps(create_app().openapi()))
    committed_schema = json.loads(SPEC_PATH.read_text())

    assert committed_schema == live_schema, REGEN_HINT
