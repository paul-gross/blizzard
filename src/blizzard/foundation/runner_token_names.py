"""The environment variable that carries one runner's hub token."""

from __future__ import annotations

import re

#: What every runner's token variable starts with; the runner's normalised name follows.
HUB_TOKEN_VARIABLE_PREFIX = "BZ_HUB_TOKEN_"

#: The variable that carries a host's one host token.
HOST_TOKEN_VARIABLE = "BZ_HOST_TOKEN"

_OUTSIDE_NAME_ALPHABET = re.compile(r"[^A-Z0-9]")


def hub_token_variable(runner_name: str) -> str:
    """``BZ_HUB_TOKEN_<NAME>``: the runner's name upper-cased with every character outside ``[A-Z0-9]``
    replaced by ``_`` — ``r-claude`` reads ``BZ_HUB_TOKEN_R_CLAUDE``."""
    return HUB_TOKEN_VARIABLE_PREFIX + _OUTSIDE_NAME_ALPHABET.sub("_", runner_name.upper())
