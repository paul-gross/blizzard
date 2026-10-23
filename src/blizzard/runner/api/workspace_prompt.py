"""The host's workspace-prompt endpoint — ``GET``/``PUT``/``DELETE /api/host/workspaces/{workspace_id}/prompt``.

The **runtime** control over a workspace's standing prompt: ``GET`` returns the
store's override when set and the static config value otherwise, ``PUT`` replaces it, and ``DELETE`` drops it.
A workspace the host does not declare answers ``404``."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.harness.workspace_prompts import EffectiveWorkspacePrompt
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN

router = APIRouter(prefix="/api/host/workspaces/{workspace_id}", tags=["host"])

#: The lane an effective workspace prompt came from — the store's runtime override, or the
#: host's configuration.
WorkspacePromptSource = Literal["override", "config"]


class WorkspacePromptResponse(WireModel):
    """The effective workspace prompt prepended to a worker spawn."""

    prompt: str
    #: Which of the two lanes produced `prompt`: the runtime override, or the host's config.
    source: Annotated[WorkspacePromptSource | Unrecognized, OPEN] = "config"


class WorkspacePromptReplacement(BaseModel):
    """A replacement workspace prompt — applies to subsequent spawns with no restart."""

    prompt: str


_UNKNOWN: dict[int | str, dict[str, Any]] = {404: {"description": "The host declares no such workspace."}}


def _response(effective: EffectiveWorkspacePrompt) -> WorkspacePromptResponse:
    return WorkspacePromptResponse(prompt=effective.prompt, source=effective.source)


@router.get("/prompt", response_model=WorkspacePromptResponse, responses=_UNKNOWN)
def read_workspace_prompt(workspace_id: str, request: Request) -> WorkspacePromptResponse:
    """The effective spawn preamble prompt: the runtime override if set, else static config."""
    wiring = RunnerWiring.of(request)
    wiring.workspace(workspace_id)
    return _response(wiring.workspace_prompt_reader().effective(workspace_id))


@router.put("/prompt", response_model=WorkspacePromptResponse, responses=_UNKNOWN)
def replace_workspace_prompt(
    workspace_id: str, request_body: WorkspacePromptReplacement, request: Request
) -> WorkspacePromptResponse:
    """Replace the runtime workspace-prompt override — effective on subsequent spawns."""
    wiring = RunnerWiring.of(request)
    wiring.workspace(workspace_id)
    wiring.workspace_prompts().replace(workspace_id, prompt=request_body.prompt)
    return WorkspacePromptResponse(prompt=request_body.prompt, source="override")


@router.delete("/prompt", response_model=WorkspacePromptResponse, responses=_UNKNOWN)
def clear_workspace_prompt(workspace_id: str, request: Request) -> WorkspacePromptResponse:
    """Drop the runtime override so the workspace's configured prompt resolves again.

    Distinct from overriding with empty text, which is itself a standing override; the response
    carries whatever the config now resolves to, effective on subsequent spawns."""
    wiring = RunnerWiring.of(request)
    wiring.workspace(workspace_id)
    wiring.workspace_prompts().clear(workspace_id)
    return _response(wiring.workspace_prompt_reader().effective(workspace_id))
