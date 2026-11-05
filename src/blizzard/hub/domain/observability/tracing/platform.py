"""The hub's action pass span names, kept out of the fleet-span dictionary's declared set.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes."""

from __future__ import annotations

ACTION_PASS_SPAN = "hub action pass"
ACTION_NAME = "blizzard.hub.action.name"
ACTION_CHOICE = "blizzard.hub.action.choice"
