"""Click parameter-source ranking shared by the CLI's mutually-resolving flags.

Ranked ``COMMANDLINE > ENVIRONMENT > DEFAULT``: a param's presence alone cannot mean
the operator chose it, since an envvar or a default always has a value.
"""

from __future__ import annotations

from click.core import ParameterSource

_SOURCE_RANK = {
    ParameterSource.COMMANDLINE: 2,
    ParameterSource.ENVIRONMENT: 1,
    ParameterSource.DEFAULT: 0,
}


def source_rank(source: ParameterSource | None) -> int:
    return _SOURCE_RANK.get(source, 0) if source is not None else 0
