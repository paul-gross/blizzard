"""The resolved ``[egress]`` config — a value; the config edge parses and renders it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from blizzard.foundation.roles import domain_model

#: The datasets an export can carry, in the order a pass writes them.
EGRESS_DATASETS = ("steps", "invocations", "events")


@domain_model
@dataclass(frozen=True)
class EgressConfig:
    """Resolved ``[egress]`` config — the fact-egress export's keys. It runs only when ``directory`` is set;
    every other key has a default that works unset."""

    directory: Path | None = None
    format: Literal["ndjson", "parquet"] = "ndjson"
    datasets: tuple[str, ...] = EGRESS_DATASETS
    sweep_seconds: int = 60
    #: How long a closed step or usage fact must have stood before it is exported; 0 exports at once.
    settle_seconds: int = 300
    batch_limit: int = 5000
    max_rows_per_file: int = 100000
    min_free_bytes: int = 1024**3
    #: The widest window a backfill may write, in seconds.
    backfill_max_window: int = 604800
    #: How a ``file_read`` event's path leaves: relative to the working directory, keyed-hashed, as stored, or omitted.
    file_paths: Literal["relative", "hashed", "absolute", "omit"] = "relative"
    #: The environment variable holding the HMAC key for hashed paths; the config never holds the secret itself.
    path_key_env: str = "BZ_EGRESS_PATH_KEY"
    #: Whether the ``events`` dataset writes only the hub's current extractor version's derivations, or every one.
    extractor_versions: Literal["current", "all"] = "current"
