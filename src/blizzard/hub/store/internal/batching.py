"""Shared id-batching for every bulk read seam (package-private).

Every batch read filtering a family query by a caller-supplied id selection batches
through :func:`id_batches` so no single ``IN (...)`` bind-parameter count grows with the
caller — `finding_store.py`'s own ``_FACTS_BATCH_SIZE`` precedent, generalized so every
seam's batch method shares one cap and one batching loop."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

#: Read live, by name, rather than captured as a default argument, so a test can lower it
#: via `monkeypatch.setattr` instead of seeding hundreds of rows to hit a batch boundary.
BATCH_SIZE = 500


def id_batches[T](ids: Sequence[T]) -> Iterator[Sequence[T]]:
    """Yield ``ids`` in slices no larger than the current :data:`BATCH_SIZE`."""
    for start in range(0, len(ids), BATCH_SIZE):
        yield ids[start : start + BATCH_SIZE]
