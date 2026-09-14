"""Shared id-batching for every bulk read seam (package-private).

Every batch read that filters a family query by a caller-supplied id selection batches
through :func:`id_batches` so no single ``IN (...)`` clause's bind-parameter count grows
with the caller — the same rationale `finding_store.py`'s own ``_FACTS_BATCH_SIZE``
documents for its private ``_facts_for_many`` loop, generalized here so every seam's
batch method shares one cap and one batching loop."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

#: The per-statement id-batch cap every batched read seam shares. Read live, by name,
#: inside :func:`id_batches`'s own body rather than captured as a default argument, so a
#: test can `monkeypatch.setattr` this module's own attribute and lower it without
#: needing to seed hundreds of rows to exercise a batch boundary.
BATCH_SIZE = 500


def id_batches[T](ids: Sequence[T]) -> Iterator[Sequence[T]]:
    """Yield ``ids`` in slices no larger than the current :data:`BATCH_SIZE`."""
    for start in range(0, len(ids), BATCH_SIZE):
        yield ids[start : start + BATCH_SIZE]
