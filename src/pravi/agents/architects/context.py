"""Context-pack helper for non-Claude architects.

Claude can browse the repo on its own via Read/Grep/Glob; other providers
can't (in our setup). For those, we pre-pack a compact slice of the repo
into the prompt: just the files the domain config explicitly designates as
`context_files`, plus a directory tree of the domain's paths so the model
knows what's in scope.

Deliberately NOT included:
  - Arbitrary file contents under domain.paths (keeps prompts predictable +
    cheap; the user opts in via domain.context_files).
  - Binaries / non-text files (skipped on read errors).
"""
from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PackedContext:
    text: str
    files_included: list[str]
    truncated: bool


def build_context(
    repo_root: Path,
    domain_paths: list[str],
    context_files: list[str],
    *,
    max_bytes: int = 80_000,
    max_tree_entries: int = 200,
) -> PackedContext:
    """Build the inline context blob for a non-Claude architect call."""
    repo_root = repo_root.expanduser().resolve()
    sections: list[str] = []
    files_included: list[str] = []
    truncated = False

    # --- Context files (explicit) ---
    files_section: list[str] = ["## Context files\n"]
    remaining = max_bytes
    for rel in context_files:
        path = (repo_root / rel).resolve()
        # Defence-in-depth: refuse paths that escape the repo root.
        try:
            path.relative_to(repo_root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Trim individual files that blow the budget.
        if len(body) > remaining:
            body = body[:remaining] + "\n\n[...truncated]"
            truncated = True
        files_section.append(f"### `{rel}`\n```\n{body}\n```\n")
        files_included.append(rel)
        remaining -= len(body) + 40
        if remaining <= 0:
            truncated = True
            break

    if len(files_section) == 1:
        files_section.append("_(no context_files configured for this domain)_\n")
    sections.append("".join(files_section))

    # --- Directory tree, filtered to domain paths ---
    tree_section: list[str] = ["\n## Files in domain (tree)\n```\n"]
    tracked = _list_tracked_files(repo_root)
    matched = [p for p in tracked if _any_match(p, domain_paths)]
    if len(matched) > max_tree_entries:
        matched = matched[:max_tree_entries]
        tree_section.append(f"(showing first {max_tree_entries} files)\n")
        truncated = True
    if not matched:
        tree_section.append("(no tracked files match the domain paths)\n")
    else:
        for p in sorted(matched):
            tree_section.append(p + "\n")
    tree_section.append("```\n")
    sections.append("".join(tree_section))

    return PackedContext(
        text="".join(sections),
        files_included=files_included,
        truncated=truncated,
    )


def _list_tracked_files(repo_root: Path) -> list[str]:
    """`git ls-files` output, relative to repo root. Empty list on failure."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _any_match(path: str, patterns: list[str]) -> bool:
    """fnmatch-style. Accepts `packages/cli/**` style globs."""
    for pat in patterns:
        # fnmatch handles `**` poorly on its own; normalize for the common case
        # where the user wrote `packages/cli/**`.
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if path == prefix.rstrip("/") or path.startswith(prefix):
                return True
            continue
        if fnmatch.fnmatch(path, pat):
            return True
    return False
