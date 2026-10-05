"""The data role a data class declares: a domain model, a persistence entity, a DTO, or a collaborator.

Each marker records its role on the class as ``__blizzard_role__`` and returns the class itself, so it
stacks on ``@dataclass(frozen=True)`` in either order and keeps its type for the type checker.
``tests/test_layering.py`` reads the markers from source and holds each role to ``bzh:data-roles``.
"""

from __future__ import annotations

from typing import Final, Literal

type DataRole = Literal["domain_model", "entity", "dto", "collaborator"]

ROLE_ATTRIBUTE: Final = "__blizzard_role__"


def _mark[C: type](cls: C, role: DataRole) -> C:
    setattr(cls, ROLE_ATTRIBUTE, role)
    return cls


def domain_model[C: type](cls: C) -> C:
    """A concept that carries rules; it holds no collaborator."""
    return _mark(cls, "domain_model")


def entity[C: type](cls: C) -> C:
    """One table row's shape, private to its store adapter — a persistence row, not an
    identity-bearing DDD entity."""
    return _mark(cls, "entity")


def dto[C: type](cls: C) -> C:
    """Data crossing a boundary — a port's input or output, a service result, a view."""
    return _mark(cls, "dto")


def collaborator[C: type](cls: C) -> C:
    """A class other code calls to do work rather than data it reads — a port's
    implementation, a driver over infrastructure, a flow over IO — whose fields show no
    collaborator for its shape to infer orchestration from."""
    return _mark(cls, "collaborator")
