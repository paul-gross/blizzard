"""Secret requests and the metadata view.

A secret's value is write-only: it rides a request as a redacted ``SecretStr`` and no
view carries it, a ciphertext, or a key id."""

from __future__ import annotations

from pydantic import BaseModel, SecretStr

from blizzard.wire.base import WireModel


class SecretCreateRequest(BaseModel):
    """Store a new secret under ``name`` — 409 when the name is taken."""

    name: str
    value: SecretStr


class SecretReplaceRequest(BaseModel):
    """Replace a secret's value, advancing its revision."""

    value: SecretStr


class RecordRefView(WireModel):
    """A configured record named by kind and key. ``label`` is the record's current slug for a slugged kind, whose
    key is its id; ``None`` for a kind keyed by its name."""

    kind: str
    key: str
    label: str | None = None


class SecretView(WireModel):
    """A secret's metadata as served by every secret route."""

    name: str
    revision: int
    replaced_at: str
    replaced_by: str
    replaced_by_name: str | None = None
    created_at: str
    retired: bool = False
    retirable: bool = True
    references: list[RecordRefView] = []
