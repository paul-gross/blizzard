"""The spawn-preamble fingerprint domain value (issue #149).

A dependency-free leaf, deliberately: this type is named in store-protocol signatures,
and a protocol module should not acquire file I/O just by being imported. *How* the
digests are computed is owned by
:func:`~blizzard.runner.harness.preamble.render_worker_preamble`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreambleFingerprint:
    """A digest of the two *standing* preamble layers a session was last sent (issue #149).

    One sha256 per layer, kept independent so a reader can tell *which* layer moved.
    Digests, never a second copy of the text (``canon:one-owner``)."""

    blizzard: str
    workspace: str
