"""The runner-local workspace-prompt endpoint — ``GET``/``PUT /api/workspace-prompt``.

The **runtime** control over the standing workspace prompt (issue #17): ``GET`` returns the
store's override when set and the static config value otherwise, and ``PUT`` replaces it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.runner.api.wiring import RunnerWiring

router = APIRouter(prefix="/api", tags=["runner"])


class WorkspacePromptResponse(BaseModel):
    """The effective workspace prompt prepended to a worker spawn. Sent in full on a fresh
    spawn; on a resumed one, only when it differs from what that session was last given —
    and then announced as updated (openapi-ts consumes this)."""

    prompt: str


class WorkspacePromptReplacement(BaseModel):
    """A replacement workspace prompt — applies to subsequent spawns with no restart."""

    prompt: str


@router.get("/workspace-prompt", response_model=WorkspacePromptResponse)
def read_workspace_prompt(request: Request) -> WorkspacePromptResponse:
    """The effective spawn preamble prompt: the runtime override if set, else static config (issue #17)."""
    wiring = RunnerWiring.of(request)
    store, config = wiring.maybe_store(), wiring.maybe_config()
    override = (
        store.workspace_prompt_override(config.workspace_id) if store is not None and config is not None else None
    )
    if override is not None:
        return WorkspacePromptResponse(prompt=override)
    return WorkspacePromptResponse(prompt=config.resolved_workspace_prompt() if config is not None else "")


@router.put("/workspace-prompt", response_model=WorkspacePromptResponse)
def replace_workspace_prompt(request_body: WorkspacePromptReplacement, request: Request) -> WorkspacePromptResponse:
    """Replace the runtime workspace-prompt override — effective on subsequent spawns (issue #17)."""
    wiring = RunnerWiring.of(request)
    store, config = wiring.store(), wiring.config()
    store.set_workspace_prompt(config.workspace_id, prompt=request_body.prompt, at=wiring.clock().now())
    return WorkspacePromptResponse(prompt=request_body.prompt)
