"""The runner-local workspace-prompt endpoint — ``GET``/``PUT /api/workspace-prompt``.

The **runtime** control over the standing workspace prompt (issue #17): ``GET`` returns the
store's override when one is set and the static config value otherwise, and ``PUT`` replaces
the override so it applies to subsequent spawns with no restart. ``PUT`` 503s with no store."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import IWriteRunnerStore

router = APIRouter(prefix="/api", tags=["runner"])


class WorkspacePromptResponse(BaseModel):
    """The effective workspace prompt prepended to a worker spawn. Sent in full on a fresh
    spawn; on a resumed one, only when it differs from what that session was last given —
    and then announced as updated (openapi-ts consumes this)."""

    prompt: str


class WorkspacePromptReplacement(BaseModel):
    """A replacement workspace prompt — applies to subsequent spawns with no restart."""

    prompt: str


def _static_prompt(request: Request) -> str:
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    return config.resolved_workspace_prompt() if config is not None else ""


@router.get("/workspace-prompt", response_model=WorkspacePromptResponse)
def read_workspace_prompt(request: Request) -> WorkspacePromptResponse:
    """The effective spawn preamble prompt: the runtime override if set, else static config (issue #17)."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    override = (
        store.workspace_prompt_override(config.workspace_id) if store is not None and config is not None else None
    )
    return WorkspacePromptResponse(prompt=override if override is not None else _static_prompt(request))


@router.put("/workspace-prompt", response_model=WorkspacePromptResponse)
def replace_workspace_prompt(request_body: WorkspacePromptReplacement, request: Request) -> WorkspacePromptResponse:
    """Replace the runtime workspace-prompt override — effective on subsequent spawns (issue #17)."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    clock = getattr(request.app.state, "clock", None)
    if store is None or config is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    store.set_workspace_prompt(config.workspace_id, prompt=request_body.prompt, at=clock.now())
    return WorkspacePromptResponse(prompt=request_body.prompt)
