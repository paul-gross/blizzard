"""The transcript lane's byte ceilings — the DEFAULTS a ``[transcripts]`` override falls
back to.

A leaf module on purpose: it imports nothing, so both the enforcing and the overriding side
can import it without a cycle."""

from __future__ import annotations

#: The runner's own per-record cap — held below the hub's `RECORD_MAX_BYTES`, which
#: REJECTS what this one merely shrinks; `tests/test_record_caps.py` asserts that ordering.
TRANSCRIPT_RECORD_MAX_BYTES = 8 * 1024 * 1024

#: The per-chunk budget — the sum of `shipped_bytes` across the chunk's segments, which
#: counts whole serialized records and so overcounts what the hub bills (its turns payload alone).
CHUNK_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024
