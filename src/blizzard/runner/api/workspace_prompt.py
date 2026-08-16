"""The runner-local workspace-prompt endpoint — ``GET``/``PUT /api/workspace-prompt``.

The **runtime** control over the standing workspace prompt (issue #17): ``GET`` returns the
store's override when set and the static config value otherwise, and ``PUT`` replaces it."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.runner.api.wiring import RunnerWiring

router = APIRouter(prefix="/api", tags=["runner"])

#: The lane an effective workspace prompt came from — the store's runtime override, or the
#: runner's own configuration.
WorkspacePromptSource = Literal["override", "config"]


class WorkspacePromptResponse(BaseModel):
    """The effective workspace prompt prepended to a worker spawn. Sent in full on a fresh
    spawn; on a resumed one, only when it differs from what that session was last given —
    and then announced as updated (openapi-ts consumes this)."""

    prompt: str
    #: Which of the two lanes produced `prompt`: the runtime override, or the runner's config.
    source: WorkspacePromptSource = "config"


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
        return WorkspacePromptResponse(prompt=override, source="override")
    prompt = config.resolved_workspace_prompt() if config is not None else ""
    return WorkspacePromptResponse(prompt=prompt, source="config")


@router.put("/workspace-prompt", response_model=WorkspacePromptResponse)
def replace_workspace_prompt(request_body: WorkspacePromptReplacement, request: Request) -> WorkspacePromptResponse:
    """Replace the runtime workspace-prompt override — effective on subsequent spawns (issue #17)."""
    wiring = RunnerWiring.of(request)
    store, config = wiring.store(), wiring.config()
    store.set_workspace_prompt(config.workspace_id, prompt=request_body.prompt, at=wiring.clock().now())
    return WorkspacePromptResponse(prompt=request_body.prompt, source="override")


@router.delete("/workspace-prompt", response_model=WorkspacePromptResponse)
def clear_workspace_prompt(request: Request) -> WorkspacePromptResponse:
    """Drop the runtime override so the runner's configured prompt resolves again (issue #344).

    Distinct from overriding with empty text, which is itself a standing override; the response
    carries whatever the config now resolves to, effective on subsequent spawns."""
    wiring = RunnerWiring.of(request)
    store, config = wiring.store(), wiring.config()
    store.clear_workspace_prompt(config.workspace_id)
    return WorkspacePromptResponse(prompt=config.resolved_workspace_prompt(), source="config")
