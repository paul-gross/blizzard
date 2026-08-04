"""The spawn-preamble fingerprint domain value (issue #149).

A dependency-free leaf, deliberately: this type is named in the runner store
protocol's signatures (:mod:`blizzard.runner.store.repository`), and a protocol module
should not acquire file I/O just by being imported — the same shape
:class:`~blizzard.runner.harness.usage.UsageSample` already has.

*How* the digests are computed is owned by
:func:`~blizzard.runner.harness.preamble.render_worker_preamble`; this module owns only
the shape they are carried in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreambleFingerprint:
    """A digest of the two *standing* preamble layers a session was last sent (issue #149).

    One sha256 per layer, kept independent rather than folded into a single digest so a
    renderer can tell *which* layer moved — the two are different events with different
    announcements. Layer 3 is deliberately absent: it is never a comparison input.

    Digests, not the prose — this value is a comparison key the runner store persists per
    session, never a second copy of the operator's text (``canon:one-owner``).
    """

    blizzard: str
    workspace: str
