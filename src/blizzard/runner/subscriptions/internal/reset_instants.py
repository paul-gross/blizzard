"""The reset-instant read shared by the Anthropic and OpenAI sampler bindings —
one parse, not two, for a vendor's ``resets_at`` given as epoch seconds or ISO-8601."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.instants import epoch_seconds_instant, iso_instant


def reset_instant(value: object) -> datetime | None:
    """A reset instant given as epoch seconds or an ISO-8601 string."""
    if isinstance(value, str):
        return iso_instant(value)
    return epoch_seconds_instant(value)
