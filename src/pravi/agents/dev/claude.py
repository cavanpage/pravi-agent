"""Dev-agent impl backed by claude-agent-sdk. Wraps `sdk_runner.runner`.

Thin shim so the rest of the codebase only talks to the `DevAgent` Protocol.
The heavy lifting (streaming messages, transcript capture, heartbeats,
budget enforcement) stays in `sdk_runner.runner` — moving it again would be
diff noise without a benefit.
"""
from __future__ import annotations

from collections.abc import Callable

from pravi.agents.protocols import DevAgent, DevRunRequest, DevRunResult, EventSink
from pravi.sdk_runner.runner import run_dev_agent


class ClaudeDevAgent(DevAgent):
    """Implements `agents.protocols.DevAgent` via claude-agent-sdk."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    async def run(
        self,
        req: DevRunRequest,
        *,
        heartbeat: Callable[[], None] | None = None,
        event_sink: EventSink | None = None,
    ) -> DevRunResult:
        # Default the model from settings if the request didn't specify one
        # and the impl was instantiated with a pin.
        if req.model is None and self.model is not None:
            req = DevRunRequest(
                cwd=req.cwd,
                system_prompt=req.system_prompt,
                user_prompt=req.user_prompt,
                max_wall_seconds=req.max_wall_seconds,
                max_turns=req.max_turns,
                max_cost_usd=req.max_cost_usd,
                model=self.model,
            )
        return await run_dev_agent(req, heartbeat=heartbeat, event_sink=event_sink)
