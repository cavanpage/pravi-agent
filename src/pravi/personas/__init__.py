"""Persona + Stack catalog — see ADR 0004.

Two orthogonal axes that together shape the dev agent's framing for a
ticket:

  * `persona` — what kind of work (frontend, backend, tester, …). The
    decompose architect picks from `ACTIVE_PERSONAS`; the full catalog
    (`ALL_PERSONAS`) includes coming_soon entries shown in the UI as
    disabled chips.
  * `stack`   — what tech the work is in (python-fastapi, java-spring,
    typescript-react, …). Inferred from `domains.yaml` or auto-detected.

Skill names attached to each persona / stack are *advisory* in v1 — they
go into the dev-agent system prompt as a hint. When claude-agent-sdk
grows a clean skill-loading API this becomes the wire.
"""

from pravi.personas.catalog import (
    ACTIVE_PERSONAS,
    ALL_PERSONAS,
    DEFAULT_PERSONA,
    Persona,
    PersonaGroup,
    PersonaStatus,
    get_persona,
)
from pravi.personas.stacks import (
    DEFAULT_STACK,
    KNOWN_STACKS,
    Stack,
    get_stack,
)

__all__ = [
    "ACTIVE_PERSONAS",
    "ALL_PERSONAS",
    "DEFAULT_PERSONA",
    "DEFAULT_STACK",
    "KNOWN_STACKS",
    "Persona",
    "PersonaGroup",
    "PersonaStatus",
    "Stack",
    "get_persona",
    "get_stack",
]
