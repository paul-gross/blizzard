"""Credential wire models — the ``/api/credentials`` record verbs and their refusals."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.record_referrer import RecordReferrer


class CredentialCreateRequest(BaseModel):
    """The ``POST /api/credentials`` body: ``forge`` names the credential's forge by id or slug, ``binding`` how it
    authenticates, ``secret`` the secret it reads, and ``settings`` the binding's own fields."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    forge: str
    binding: str
    secret: str
    settings: dict[str, Any] = {}


class CredentialPatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused. A
    credential's ``forge`` and ``binding`` never change; a patch naming either is refused."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    secret: str | None = None
    settings: dict[str, Any] | None = None
    forge: str | None = None
    binding: str | None = None


class CredentialSummary(WireModel):
    """One credential — the ``GET /api/credentials`` listing row and every record verb's view. ``authority`` is what
    its binding acts as, derived from its secret and settings."""

    credential_id: str
    slug: str
    name: str
    forge_id: str
    forge_slug: str
    forge_name: str
    binding: str
    secret_name: str
    settings: dict[str, Any]
    authority: str
    revision: int
    created_at: str
    created_by: str
    created_by_name: str | None = None
    retired: bool


class CredentialReferenceView(WireModel):
    """A repository or work source naming the credential, by kind, id and slug."""

    kind: str
    key: str
    label: str


class CredentialDocument(CredentialSummary):
    """One credential with every repository and work source naming it, retired ones included."""

    referrers: list[CredentialReferenceView]


class CredentialListItem(CredentialSummary):
    """One ``GET /api/credentials`` row: the credential, with how many enabled repositories and work sources name it."""

    referrer_count: int


class CredentialSettingView(WireModel):
    """One setting a binding takes, by ``key``, with the values it accepts."""

    key: str
    choices: list[str]


class CredentialBindingView(WireModel):
    """A binding a credential may name: its ``name``, the kind of forge it authenticates against, and its settings."""

    name: str
    forge_kind: str
    settings: list[CredentialSettingView]


class ForgeBindingsView(WireModel):
    """An enabled forge, by id, with the name of every binding a credential on it may name."""

    forge_id: str
    bindings: list[str]


class CredentialsListView(WireModel):
    """Every stored credential by slug — ``GET /api/credentials`` — every binding a credential may name, and the
    bindings each enabled forge allows."""

    credentials: list[CredentialListItem]
    bindings: list[CredentialBindingView]
    forge_bindings: list[ForgeBindingsView]


CREDENTIAL_NOT_FOUND: Final = "urn:blizzard:problem:credential-not-found"


CREDENTIAL_IN_USE: Final = "urn:blizzard:problem:credential-in-use"


CREDENTIAL_SLUG_TAKEN: Final = "urn:blizzard:problem:credential-slug-taken"


CREDENTIAL_AUTHORITY_TAKEN: Final = "urn:blizzard:problem:credential-authority-taken"


CREDENTIAL_CAPABILITY_MISSING: Final = "urn:blizzard:problem:credential-capability-missing"


class CredentialNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:credential-not-found"]]):
    """A path names a credential the tenant does not hold."""


class CredentialInUseProblem(ProblemDetails[Literal["urn:blizzard:problem:credential-in-use"]]):
    """Enabled records still name the credential; ``referrers`` lists each."""

    referrers: list[RecordReferrer]


class CredentialSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:credential-slug-taken"]]):
    """Another credential holds ``slug``."""

    slug: str


class CredentialAuthorityTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:credential-authority-taken"]]):
    """Credential ``holder`` on the forge already acts as ``authority``."""

    holder: str
    authority: str


class CredentialCapabilityMissingProblem(ProblemDetails[Literal["urn:blizzard:problem:credential-capability-missing"]]):
    """A credential edit would no longer offer what an enabled repository acting with it needs: ``referrers`` lists
    each such repository, and ``missing`` the capabilities they would lose."""

    referrers: list[RecordReferrer]
    missing: list[str]


CREDENTIAL_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        CREDENTIAL_NOT_FOUND,
        CREDENTIAL_IN_USE,
        CREDENTIAL_SLUG_TAKEN,
        CREDENTIAL_AUTHORITY_TAKEN,
        CREDENTIAL_CAPABILITY_MISSING,
    }
)
