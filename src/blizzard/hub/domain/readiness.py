"""Alias of the shared readiness rule, kept only while ``hub/app.py`` still imports it here."""

from __future__ import annotations

from blizzard.foundation.store.readiness import Readiness, ReadinessService

__all__ = ["Readiness", "ReadinessService"]
