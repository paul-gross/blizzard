"""The hub's ``run:`` step and action pass span names, kept out of the fleet-span dictionary's declared set.

Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/instrumentation.md`` §Attributes."""

from __future__ import annotations

RUN_STEP_SPAN = "hub run step"
RUN_STEP_NAME = "blizzard.hub.run_step.name"
RUN_STEP_EXIT_CODE = "blizzard.hub.run_step.exit_code"

ACTION_PASS_SPAN = "hub action pass"
ACTION_NAME = "blizzard.hub.action.name"
ACTION_CHOICE = "blizzard.hub.action.choice"
