"""Hub-wide settings HTTP models, distinct from tenant settings."""

from __future__ import annotations

from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.hub_setting_source import HubSettingSource
from blizzard.foundation.settings_record.vocabulary import Door, Op
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.config import FieldChangeView
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.vocabulary import OPEN


def _acyclic_document(value: object) -> object:
    parents: set[int] = set()
    stack: list[tuple[object, bool]] = [(value, False)]
    while stack:
        item, leaving = stack.pop()
        if not isinstance(item, (dict, list)):
            continue
        container = cast(dict[object, object] | list[object], item)
        identity = id(container)
        if leaving:
            parents.remove(identity)
            continue
        if identity in parents:
            raise ValueError("settings document contains a cycle")
        parents.add(identity)
        stack.append((container, True))
        children = list(container.values()) if isinstance(container, dict) else container
        for child in children:
            stack.append((child, False))
    return value


#: ``GET /api/admin/settings/export``: the hub's non-default pass-lifetime settings as a nested TOML-shaped document.
SettingsExportDocument = Annotated[dict[str, object], BeforeValidator(_acyclic_document)]
SETTINGS_EXPORT_ADAPTER: TypeAdapter[SettingsExportDocument] = TypeAdapter(SettingsExportDocument)


class HubSettingRow(WireModel):
    key: str
    value: object
    source: Annotated[HubSettingSource | Unrecognized, OPEN]
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
    diff: list[FieldChangeView]


class HubSettingsChanges(WireModel):
    changes: list[HubSettingsChange]
    next_before: int | None


SETTING_UNKNOWN: Final = "urn:blizzard:problem:setting-unknown"

SETTING_NOT_LIVE: Final = "urn:blizzard:problem:setting-not-live"

SETTINGS_INVALID: Final = "urn:blizzard:problem:settings-invalid"


class SettingUnknownProblem(ProblemDetails[Literal["urn:blizzard:problem:setting-unknown"]]):
    """A settings write names a key the hub does not declare as settable."""

    code: Literal["setting_unknown"]
    errors: dict[str, str] = {}


class SettingNotLiveProblem(ProblemDetails[Literal["urn:blizzard:problem:setting-not-live"]]):
    """A settings write names a boot setting, which is edited in the hub's config file and takes a restart."""

    code: Literal["setting_not_live"]
    errors: dict[str, str] = {}


class SettingsInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:settings-invalid"]]):
    """A contradictory or invalid settings write; ``errors`` names each offending key."""

    code: Literal["settings_invalid"]
    errors: dict[str, str] = {}


HUB_SETTINGS_PROBLEM_TYPES: frozenset[str] = frozenset({SETTING_UNKNOWN, SETTING_NOT_LIVE, SETTINGS_INVALID})
