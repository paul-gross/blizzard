"""The outcome of one delivery to the hub."""

from __future__ import annotations

from typing import Literal

#: The result of one delivery: ``recorded`` durably means it, materialized now or replayed; ``invalid`` is rejected.
DeliveryOutcome = Literal["recorded", "invalid"]
