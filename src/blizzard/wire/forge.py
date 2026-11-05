"""Forge wire models — the ``/api/forges`` record verbs, and how a repository or work-source write records the
address fields the forge now carries."""

from __future__ import annotations

from typing import Any, ClassVar, Self, cast

from pydantic import BaseModel, ConfigDict, ModelWrapValidatorHandler, PrivateAttr, model_validator
from pydantic.config import JsonDict

from blizzard.wire.base import WireModel

#: The JSON Schema mark of a field kept on the wire only until a later release removes it.
DEPRECATED_FIELD: JsonDict = {"deprecated": True}


class OldFieldWarning(WireModel):
    """A warning a write once answered about an address field — ``field``, and the forge it resolved to, by id and
    slug. No write answers one now."""

    field: str
    forge_id: str
    forge_slug: str
    message: str


class MovedFieldsRequest(BaseModel):
    """A request body that records which of ``moved_fields`` — the address fields the forge now carries — it was sent,
    with the values sent, as ``moved``, so the domain can refuse them naming the forge. None of them is part of the
    body's schema, and every other undeclared field is refused as ever."""

    model_config = ConfigDict(extra="forbid")

    moved_fields: ClassVar[tuple[str, ...]] = ()
    _moved: tuple[tuple[str, object], ...] = PrivateAttr(default=())

    @model_validator(mode="wrap")
    @classmethod
    def _record_moved(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        moved: tuple[tuple[str, object], ...] = ()
        if isinstance(data, dict):
            body = cast(dict[str, object], data)
            moved = tuple((name, body[name]) for name in cls.moved_fields if name in body)
            data = {key: value for key, value in body.items() if key not in cls.moved_fields}
        model = handler(data)
        model._moved = moved
        return model

    @property
    def moved(self) -> tuple[tuple[str, object], ...]:
        """Each moved field the body carried, in the order ``moved_fields`` lists them, with the value sent."""
        return self._moved


class ForgeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    kind: str
    api_base: str
    web_base: str | None = None


class ForgePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    api_base: str | None = None
    web_base: str | None = None


class ForgeReferenceView(WireModel):
    kind: str
    key: str
    label: str | None = None


class ForgeSummary(WireModel):
    forge_id: str
    slug: str
    name: str
    kind: str
    api_base: str
    web_base: str | None
    web_origin: str
    revision: int
    created_at: str
    created_by: str
    created_by_name: str | None = None
    retired: bool


class ForgeBudgetView(WireModel):
    """One rate budget's last reading of one bucket: the secret its calls go out under (``None`` for the anonymous
    budget), and the limit, remaining and reset the forge last answered with."""

    secret_name: str | None
    bucket: str
    limit: int
    remaining: int
    reset: str


class ForgeDetail(ForgeSummary):
    repositories: list[ForgeReferenceView]
    work_sources: list[ForgeReferenceView]
    #: The capabilities the forge offers, by name — what a repository's landing policy is checked against.
    offered: list[str] = []
    #: Each budget's last reading, held in memory since the hub started; empty until the forge has been heard from.
    budgets: list[ForgeBudgetView]


class ForgesListView(WireModel):
    forges: list[ForgeSummary]
