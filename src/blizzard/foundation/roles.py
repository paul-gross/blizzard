"""The data role a data class declares: a domain model, a DTO, an adapter model, or a collaborator.

Each marker records its role on the class as ``__blizzard_role__`` and returns the class itself, so it
stacks on ``@dataclass(frozen=True)`` in either order and keeps its type for the type checker.
``tests/test_layering.py`` reads the markers from source and holds each role to ``bzh:data-roles``.
"""

from __future__ import annotations

from typing import Final, Literal

type DataRole = Literal["domain_model", "dto", "adapter_model", "collaborator"]

ROLE_ATTRIBUTE: Final = "__blizzard_role__"


def _mark[C: type](cls: C, role: DataRole) -> C:
    setattr(cls, ROLE_ATTRIBUTE, role)
    return cls


def domain_model[C: type](cls: C) -> C:
    """The ``domain_model`` role — ``bzh:data-roles`` §Roles."""
    return _mark(cls, "domain_model")


def dto[C: type](cls: C) -> C:
    """The ``dto`` role — ``bzh:data-roles`` §Roles."""
    return _mark(cls, "dto")


def adapter_model[C: type](cls: C) -> C:
    """The ``adapter_model`` role — ``bzh:data-roles`` §Roles."""
    return _mark(cls, "adapter_model")


def collaborator[C: type](cls: C) -> C:
    """The ``collaborator`` role — ``bzh:data-roles`` §Roles."""
    return _mark(cls, "collaborator")
