"""Parse the architect's ```yaml block from the decomposition response.

Tolerant by design — if the architect strays from the schema we surface a
useful error rather than crashing. The raw markdown is preserved upstream so
the user can fix mistakes in the UI.
"""
from __future__ import annotations

import re

import yaml

from pravi.agents.protocols import DecomposedFeature, DecomposedTask

_FENCED_YAML = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def parse_decomposition(raw_md: str) -> tuple[list[DecomposedFeature], list[str]]:
    """Return (features, errors). On any failure, features is [] and errors
    is non-empty; raw_md stays available to the caller for UI editing."""
    if not raw_md or not raw_md.strip():
        return [], ["empty response"]

    match = _FENCED_YAML.search(raw_md)
    if not match:
        return [], ["no ```yaml block found in architect output"]

    yaml_text = match.group(1)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return [], [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return [], ["YAML root must be a mapping with a `features` key"]
    features_raw = data.get("features")
    if not isinstance(features_raw, list) or not features_raw:
        return [], ["`features` must be a non-empty list"]

    features: list[DecomposedFeature] = []
    errors: list[str] = []
    for i, f in enumerate(features_raw):
        if not isinstance(f, dict):
            errors.append(f"features[{i}] must be a mapping")
            continue
        title = (f.get("title") or "").strip()
        if not title:
            errors.append(f"features[{i}].title is required")
            continue
        description = str(f.get("description") or "").strip()
        domain = f.get("domain")
        if domain is not None:
            domain = str(domain).strip() or None
        # depends_on: list of feature titles (strings). Resolved to row IDs
        # at materialization time; here we just parse + normalize.
        depends_raw = f.get("depends_on") or []
        if not isinstance(depends_raw, list):
            errors.append(
                f"features[{i}] ({title!r}).depends_on must be a list of strings"
            )
            depends_raw = []
        depends_on: list[str] = []
        for d in depends_raw:
            s = str(d or "").strip()
            if s:
                depends_on.append(s)
        tasks_raw = f.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            errors.append(f"features[{i}] ({title!r}) must have a non-empty tasks list")
            continue
        tasks: list[DecomposedTask] = []
        for j, t in enumerate(tasks_raw):
            if not isinstance(t, dict):
                errors.append(f"features[{i}].tasks[{j}] must be a mapping")
                continue
            t_title = (t.get("title") or "").strip()
            if not t_title:
                errors.append(f"features[{i}].tasks[{j}].title is required")
                continue
            t_desc = str(t.get("description") or "").strip()
            tasks.append(DecomposedTask(title=t_title, description=t_desc))
        if tasks:
            features.append(
                DecomposedFeature(
                    title=title,
                    description=description,
                    domain=domain,
                    tasks=tasks,
                    depends_on=depends_on,
                )
            )

    if not features and not errors:
        errors.append("no valid features parsed")

    # Validate intra-list references — every depends_on must point at a
    # feature title that exists in this decomposition. Done here so the UI
    # surfaces it before approval as well.
    known_titles = {f.title for f in features}
    for f in features:
        for dep_title in f.depends_on:
            if dep_title not in known_titles:
                errors.append(
                    f"feature {f.title!r} depends_on unknown title {dep_title!r}"
                )

    # Cycle detection (Kahn-style — if any node has unresolved deps after
    # a full pass, a cycle exists).
    title_to_deps = {f.title: set(f.depends_on) for f in features}
    resolved: set[str] = set()
    while True:
        next_resolved = {
            t for t, ds in title_to_deps.items() if t not in resolved and ds.issubset(resolved)
        }
        if not next_resolved:
            break
        resolved |= next_resolved
    unresolved = set(title_to_deps) - resolved
    if unresolved:
        errors.append(
            f"dependency cycle involving feature(s): {sorted(unresolved)}"
        )

    return features, errors
