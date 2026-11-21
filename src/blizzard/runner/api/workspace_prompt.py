"""The host's workspace-prompt endpoint — ``GET``/``PUT``/``DELETE /api/host/workspaces/{workspace_id}/prompt``.

The prompt is the workspace record's ``prompt``, ``prompt_file`` and ``prompt_package`` settings: ``GET`` returns the
prompt they resolve to, ``PUT`` sets ``prompt`` and returns the other two to their defaults, and ``DELETE`` returns
all three to their defaults. Each write is the record's own edit at its current revision, on the human lane the
host's reads share rather than behind a superuser session. A workspace the host holds no record of answers ``404``."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.api.federation import require_human_api
from blizzard.runner.api.host_records import record_write
from blizzard.runner.api.problems import problem
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import record_prompt
from blizzard.runner.host_records.model import HostRecord, RecordKind, RecordUnknown
from blizzard.runner.host_records.prompt import PromptChange
from blizzard.runner.host_records.service import HostRecordService
from blizzard.runner.hosting.model import API
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN

router = APIRouter(prefix="/api/host/workspaces/{workspace_id}", tags=["host"])

#: The effective prompt's source: its record's settings, none stated, or (from an older host) its override row.
WorkspacePromptSource = Literal["override", "config", "default"]


class WorkspacePromptResponse(WireModel):
    """The effective workspace prompt prepended to a worker spawn."""

    prompt: str
    source: Annotated[WorkspacePromptSource | Unrecognized, OPEN] = "config"


class WorkspacePromptReplacement(BaseModel):
    """A replacement workspace prompt — taken up with no restart at the host's next pass, by every spawn after it."""

    prompt: str


_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/RunnerProblem"}}}
_SETTINGS_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/SettingsProblem"}}}
_READ: dict[int | str, dict[str, Any]] = {
    404: {"description": "The host holds no record of the workspace", "content": _PROBLEM},
    409: {"description": "The workspace's prompt does not resolve", "content": _PROBLEM},
}
_WRITE: dict[int | str, dict[str, Any]] = {
    **_READ,
    409: {
        "description": "The workspace's prompt does not resolve, or another write moved the record first",
        "content": {
            "application/problem+json": {
                "schema": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/RunnerProblem"},
                        {"$ref": "#/components/schemas/SettingsProblem"},
                    ]
                }
            }
        },
    },
    422: {
        "description": "The host file would fail at boot with it, or the request is malformed",
        "content": {
            **_SETTINGS_PROBLEM,
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        },
    },
}


def _response(wiring: RunnerWiring, record: HostRecord) -> WorkspacePromptResponse | JSONResponse:
    try:
        effective = record_prompt(record, wiring.config().root)
    except ConfigError as exc:
        return problem(409, "prompt_unresolved", str(exc))
    return WorkspacePromptResponse(prompt=effective.prompt, source=effective.source)


def _write(
    request: Request, workspace_id: str, change: PromptChange, session: RunnerSession
) -> WorkspacePromptResponse | JSONResponse:
    wiring = RunnerWiring.of(request)

    def write(service: HostRecordService) -> HostRecord:
        record = service.held(workspace_id)
        return service.edit(
            record, change.sets, change.unsets, if_match=record.revision, actor=session.username, door=API
        )

    return record_write(write, wiring.host_records(RecordKind.WORKSPACE), lambda written: _response(wiring, written))


@router.get("/prompt", response_model=WorkspacePromptResponse, responses=_READ)
def read_workspace_prompt(workspace_id: str, request: Request) -> WorkspacePromptResponse | JSONResponse:
    """The effective spawn preamble prompt the workspace's record resolves to."""
    wiring = RunnerWiring.of(request)
    try:
        record = wiring.host_records(RecordKind.WORKSPACE).held(workspace_id)
    except RecordUnknown as exc:
        return problem(404, "workspace_unknown", str(exc))
    return _response(wiring, record)


@router.put("/prompt", response_model=WorkspacePromptResponse, responses=_WRITE)
def replace_workspace_prompt(
    workspace_id: str,
    request_body: WorkspacePromptReplacement,
    request: Request,
    session: Annotated[RunnerSession, Depends(require_human_api)],
) -> WorkspacePromptResponse | JSONResponse:
    """Set the workspace's ``prompt``, returning ``prompt_file`` and ``prompt_package`` to their defaults — the host's
    next pass rebuilds the workspace, and every spawn after it reads the new prompt."""
    return _write(request, workspace_id, PromptChange.stating("prompt", request_body.prompt), session)


@router.delete("/prompt", response_model=WorkspacePromptResponse, responses=_WRITE)
def clear_workspace_prompt(
    workspace_id: str, request: Request, session: Annotated[RunnerSession, Depends(require_human_api)]
) -> WorkspacePromptResponse | JSONResponse:
    """Return the workspace's three prompt settings to their defaults, so it carries no standing prompt — the host's
    next pass rebuilds the workspace, and every spawn after it carries none."""
    return _write(request, workspace_id, PromptChange.cleared(), session)
