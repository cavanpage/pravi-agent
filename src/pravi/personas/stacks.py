"""Stack catalog — the second axis to persona. See ADR 0004.

Open-set: the decompose architect may mint new stack slugs, and unknown
stacks resolve to `unknown` (no additional skills loaded). The starter
list below covers the common combos.

`additional_skills` extends the persona's `baseline_skills`. The union
is what the dev-agent system prompt surfaces as a hint.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stack:
    slug: str
    name: str
    description: str = ""
    # Claude Skills (by name) that should be loaded on top of the
    # persona's baseline when this stack applies. Advisory in v1.
    additional_skills: list[str] = field(default_factory=list)
    # Brief autodetect hints, surfaced in docs / logs. Not executable —
    # auto-detection logic, if added, lives in a separate detector.
    detect_hints: list[str] = field(default_factory=list)


_PYTHON_FASTAPI = Stack(
    slug="python-fastapi",
    name="Python · FastAPI",
    additional_skills=["python", "fastapi", "pytest"],
    detect_hints=["pyproject.toml with fastapi in deps"],
)
_PYTHON_DJANGO = Stack(
    slug="python-django",
    name="Python · Django",
    additional_skills=["python", "django", "pytest"],
    detect_hints=["manage.py + django in deps"],
)
_PYTHON_STDLIB = Stack(
    slug="python-stdlib",
    name="Python (no web framework)",
    additional_skills=["python", "pytest"],
    detect_hints=["pyproject.toml without a web framework"],
)
_TS_REACT = Stack(
    slug="typescript-react",
    name="TypeScript · React",
    additional_skills=["typescript", "react"],
    detect_hints=["package.json with react in deps"],
)
_TS_VUE = Stack(
    slug="typescript-vue",
    name="TypeScript · Vue",
    additional_skills=["typescript", "vue"],
    detect_hints=["package.json with vue in deps"],
)
_TS_NODE = Stack(
    slug="typescript-node",
    name="TypeScript · Node (no UI framework)",
    additional_skills=["typescript", "node"],
    detect_hints=["package.json, no UI framework"],
)
_JAVA_SPRING = Stack(
    slug="java-spring",
    name="Java · Spring Boot",
    additional_skills=["java", "spring-boot", "junit"],
    detect_hints=["pom.xml or build.gradle with spring deps"],
)
_GO = Stack(
    slug="go-stdlib",
    name="Go",
    additional_skills=["go", "go-test"],
    detect_hints=["go.mod"],
)
_RUST = Stack(
    slug="rust",
    name="Rust",
    additional_skills=["rust", "cargo-test"],
    detect_hints=["Cargo.toml"],
)
_MARKDOWN = Stack(
    slug="markdown",
    name="Markdown / docs",
    additional_skills=[],
    detect_hints=["persona=tech_writer"],
)
_UNKNOWN = Stack(
    slug="unknown",
    name="Unknown / generic",
    additional_skills=[],
    detect_hints=["fallthrough"],
)


KNOWN_STACKS: list[Stack] = [
    _PYTHON_FASTAPI,
    _PYTHON_DJANGO,
    _PYTHON_STDLIB,
    _TS_REACT,
    _TS_VUE,
    _TS_NODE,
    _JAVA_SPRING,
    _GO,
    _RUST,
    _MARKDOWN,
    _UNKNOWN,
]

_BY_SLUG: dict[str, Stack] = {s.slug: s for s in KNOWN_STACKS}

DEFAULT_STACK: Stack = _UNKNOWN


def get_stack(slug: str | None) -> Stack:
    """Resolve a slug to a Stack. Unknown or null → DEFAULT_STACK
    (`unknown` → no additional skills loaded)."""
    if not slug:
        return DEFAULT_STACK
    return _BY_SLUG.get(slug, DEFAULT_STACK)
