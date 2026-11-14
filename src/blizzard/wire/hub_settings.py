"""Hub-wide settings HTTP models, distinct from tenant settings."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.settings_record.vocabulary import Door, Op
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.vocabulary import OPEN


class HubSettingRow(WireModel):
    key: str
    value: object
    source: Annotated[Literal["entry", "default"] | Unrecognized, OPEN]
    lifetime: Annotated[Lifetime | Unrecognized, OPEN]
    revision: int | None = None
    written_at: str | None = None
    written_by: str | None = None


class HubSettingsView(WireModel):
    revision: int
    settings: list[HubSettingRow]


class HubSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sets: dict[str, object] = Field(default_factory=dict, alias="set")
    unset: list[str] = Field(default_factory=list)


class HubSettingsChange(WireModel):
    id: int
    revision: int
    recorded_at: str
    actor: str
    door: Annotated[Door | Unrecognized, OPEN]
    op: Annotated[Op | Unrecognized, OPEN]
    apply_id: str | None
    diff: list[dict[str, object]]


class HubSettingsChanges(WireModel):
    changes: list[HubSettingsChange]
    next_before: int | None


class HubSettingsInvalidProblem(ProblemDetails[str]):
    """An unrecognized, non-live, contradictory or invalid settings write."""

    code: str
    errors: dict[str, str] = {}
