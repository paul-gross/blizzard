"""``--project``: the project a hub verb acts in or reads, by id or slug, sent as written for the hub to resolve."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])

#: The query parameter, and the nested-path segment's name, the hub reads a named project from.
PROJECT_PARAM = "project"

_RECORD_HELP = (
    "The project the record belongs to, by id or slug. Needed only when the tenant holds several projects; with one, "
    "the record goes to it."
)
_LISTING_HELP = "Show only this project's records, by id or slug. Without it, every project of the tenant is shown."


def project_option(*, required_by_hub: bool) -> Callable[[F], F]:
    """The ``--project`` option, passed to the verb as ``project`` (``None`` when not given). ``required_by_hub``
    marks a verb whose call needs a project — a record create: the option stays optional on the command line, since
    a tenant with one project needs none, and the hub refuses a call that names none among several projects. A
    listing verb's option narrows what it reads."""
    return click.option(
        "--project",
        "project",
        default=None,
        metavar="PROJECT",
        help=_RECORD_HELP if required_by_hub else _LISTING_HELP,
    )


def project_query(project: str | None) -> dict[str, str]:
    """The query parameters naming ``project``: none when it is ``None``."""
    return {PROJECT_PARAM: project} if project is not None else {}
