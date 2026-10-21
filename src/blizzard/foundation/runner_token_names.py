"""The environment variable that carries one runner's hub token — a rule the runner and the hub CLI must
evaluate identically, and the hub cannot import ``blizzard.runner`` (``bzh:shared-kernel``)."""

from __future__ import annotations

import re

#: What every runner's token variable starts with; the runner's normalised name follows.
HUB_TOKEN_VARIABLE_PREFIX = "BZ_HUB_TOKEN_"

#: The variable a host reads its one host token from; the hub CLI prints the same name in the `.env` line it mints.
HOST_TOKEN_VARIABLE = "BZ_HOST_TOKEN"

_OUTSIDE_NAME_ALPHABET = re.compile(r"[^A-Z0-9]")


def hub_token_variable(runner_name: str) -> str:
    """``BZ_HUB_TOKEN_<NAME>``: the runner's name upper-cased with every character outside ``[A-Z0-9]``
    replaced by ``_`` — ``r-claude`` reads ``BZ_HUB_TOKEN_R_CLAUDE``."""
    return HUB_TOKEN_VARIABLE_PREFIX + _OUTSIDE_NAME_ALPHABET.sub("_", runner_name.upper())
