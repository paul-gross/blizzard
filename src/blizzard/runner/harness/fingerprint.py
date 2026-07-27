"""The spawn-preamble fingerprint domain value (issue #149).

A dependency-free leaf, deliberately: the runner **store** protocol
(:mod:`blizzard.runner.store.repository`) names this type in its read/write signatures,
and a protocol module should not acquire file I/O or a transitive environments-provider
import just by being imported. That is the shape
:class:`~blizzard.runner.harness.usage.UsageSample` already has, and the reason this value
lives here rather than beside the renderer that produces it
(:mod:`blizzard.runner.harness.preamble`, which reads six packaged markdown files at
import time).

The renderer owns *how* the digests are computed — always over the layers' resolved,
post-``strip()`` input text, stated once in
:func:`~blizzard.runner.harness.preamble.render_worker_preamble`. This module owns only
the shape they are carried in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreambleFingerprint:
    """A digest of the two *standing* preamble layers a session was last sent (issue #149).

    One sha256 per layer, kept independent rather than folded into a single digest so a
    renderer can tell *which* layer moved: layer 1 changing (a ``runner_prompt`` edit or a
    packaged-default upgrade, both requiring a runner restart) and layer 2 changing (a live
    ``PUT /api/workspace-prompt``) are different events with different announcements, and
    the common case is layer 2 alone.

    Layer 3 is deliberately absent: the facts table is re-rendered per attempt around a
    freshly minted ``lease_id``, so it is never a comparison input and is never elided.

    Digests, not the prose — this value is a comparison key the runner store persists per
    session, never a second copy of the operator's text (``canon:one-owner``).
    """

    blizzard: str
    workspace: str
