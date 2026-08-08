"""Click parameter-source ranking shared by the CLI's mutually-resolving flags.

Ranked ``COMMANDLINE > ENVIRONMENT > DEFAULT``: a param's presence alone cannot mean
the operator chose it, since an envvar or a default always has a value."""

from __future__ import annotations

from dataclasses import dataclass

import click
from click.core import ParameterSource

_SOURCE_RANK = {
    ParameterSource.COMMANDLINE: 2,
    ParameterSource.ENVIRONMENT: 1,
    ParameterSource.DEFAULT: 0,
}


@dataclass(frozen=True, order=True)
class ParamSource:
    """Where one click parameter's value came from, as a comparable rank."""

    rank: int

    @classmethod
    def of(cls, param: str) -> ParamSource:
        source = click.get_current_context().get_parameter_source(param)
        return cls(_SOURCE_RANK.get(source, 0) if source is not None else 0)

    @property
    def on_commandline(self) -> bool:
        return self.rank == _SOURCE_RANK[ParameterSource.COMMANDLINE]
