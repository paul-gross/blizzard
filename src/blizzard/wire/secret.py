"""Secret requests and the metadata view.

A secret's value is write-only: it rides a request as a redacted ``SecretStr`` and no
view carries it, a ciphertext, or a key id."""

from __future__ import annotations

from pydantic import BaseModel, SecretStr


class SecretCreateRequest(BaseModel):
    """Store a new secret under ``name`` — 409 when the name is taken."""

    name: str
    value: SecretStr


class SecretReplaceRequest(BaseModel):
    """Replace a secret's value, advancing its revision."""

    value: SecretStr


class RecordRefView(BaseModel):
    """A configured record named by kind and key."""

    kind: str
    key: str


class SecretView(BaseModel):
    """A secret's metadata as served by every secret route."""

    name: str
    revision: int
    replaced_at: str
    replaced_by: str
    created_at: str
    retired: bool = False
    references: list[RecordRefView] = []
