"""Click parameter-source ranking shared by the CLI's mutually-resolving flags.

Winter's per-env ``[env.<name>.vars]`` band exports config ambiently across a whole
feature env, so a param's mere presence can't mean "the operator chose it" — an envvar
or default always *has* a value. Ranked ``COMMANDLINE > ENVIRONMENT > DEFAULT`` lets a
command tell an explicit flag from an ambient one apart, and treat a genuine
command-line tie — not an ambient default — as the ambiguous case worth failing on.
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
