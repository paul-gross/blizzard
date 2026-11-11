"""Runner control response."""

from __future__ import annotations

from pydantic import BaseModel


class RunnerControlView(BaseModel):
    """The runner singleton's derived pause state."""

    runner_id: str | None  # the hub-minted id of the latest registration; None before the first
    runner_name: str  # the name the hub recorded then, else the configured one
    local_paused: bool  # this runner's own brake
    hub_paused: bool  # the hub's brake, as last mirrored
    paused: bool  # effective: the OR of the two
