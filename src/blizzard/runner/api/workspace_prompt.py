"""The runner-local workspace-prompt endpoint — ``GET``/``PUT /api/workspace-prompt``.

The runner prepends a standing workspace prompt to every worker spawn (issue #17). Its
static source is config (``blizzard-runner.toml``, loaded at ``host`` startup); this edge
is the **runtime** control over it:

* ``GET`` returns the effective prompt — the store's runtime override when one has been
  set, else the static config value.
* ``PUT`` replaces the override in the store, so it applies to subsequent spawns with no
  restart (the loop reads the override at each spawn).

The edge is read-only over its wiring (``bzh:controller-read-only``): it reads/writes
through the store the ``host`` composition root wired on ``app.state`` and reads the static
fallback off ``app.state.config``. ``PUT`` needs the store, so on the store-free app (OpenAPI
export / unit tests) it answers 503 rather than pretending; ``GET`` still reports the static
config value there. The CLI/operator is a pure client — it never opens the store itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import IWriteRunnerStore

router = APIRouter(prefix="/api", tags=["runner"])


class WorkspacePromptResponse(BaseModel):
    """The effective workspace prompt prepended to every worker spawn (openapi-ts consumes this)."""

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
