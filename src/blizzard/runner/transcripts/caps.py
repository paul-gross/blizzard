"""The transcript lane's byte ceilings — the DEFAULTS a ``[transcripts]`` override falls
back to (blizzard#338).

A leaf module on purpose: both the enforcement site (`loop.transcript_pump`) and the
config that overrides it (`runner.config`, which renders each default into its scaffolded
template) need these, and the pump imports the config, so the config cannot import the pump."""

from __future__ import annotations

#: The runner's own per-record cap (D4) — held below the hub's `RECORD_MAX_BYTES`, which
#: REJECTS what this one merely shrinks; `test_record_caps.py` asserts that ordering.
TRANSCRIPT_RECORD_MAX_BYTES = 8 * 1024 * 1024

#: The per-chunk budget (D4) — the sum of `shipped_bytes` across the chunk's segments, which
#: counts whole serialized records and so overcounts what the hub bills (its turns payload alone).
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024
