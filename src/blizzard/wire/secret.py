"""Secret requests and the metadata view.

A secret's value is write-only: it rides a request as a redacted ``SecretStr`` and no
view carries it, a ciphertext, or a key id."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, SecretStr

from blizzard.wire.base import WireModel
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails
from blizzard.wire.record_referrer import RecordReferrer


class SecretAlreadyExistsProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-already-exists"]]):
    """A create named a secret the tenant already holds."""


class SecretNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-not-found"]]):
    """The named secret does not exist in the tenant."""


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


SECRET_ALREADY_EXISTS: Final = "urn:blizzard:problem:secret-already-exists"


SECRET_NOT_FOUND: Final = "urn:blizzard:problem:secret-not-found"


SECRET_RETIRED: Final = "urn:blizzard:problem:secret-retired"


SECRET_NAME_INVALID: Final = "urn:blizzard:problem:secret-name-invalid"


SECRET_VALUE_BLANK: Final = "urn:blizzard:problem:secret-value-blank"


SECRET_IN_USE: Final = "urn:blizzard:problem:secret-in-use"


class SecretRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-retired"]]):
    """The secret is retired and must be enabled first."""


class SecretNameInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-name-invalid"]]):
    """The secret name is not a valid name."""


class SecretValueBlankProblem(ProblemDetails[Literal["urn:blizzard:problem:secret-value-blank"]]):
    """The secret value is blank."""


class SecretInUseDetail(WireModel):
    code: Literal["secret_in_use"] = "secret_in_use"
    message: str
    referrers: list[RecordReferrer]


class SecretInUseProblem(CommonProblemMembers[Literal["urn:blizzard:problem:secret-in-use"], SecretInUseDetail]):
    """Active records still reference the secret."""

    status: int | None = None


SECRET_PROBLEM_TYPES: frozenset[str] = frozenset(
    {SECRET_ALREADY_EXISTS, SECRET_NOT_FOUND, SECRET_RETIRED, SECRET_NAME_INVALID, SECRET_VALUE_BLANK, SECRET_IN_USE}
)
