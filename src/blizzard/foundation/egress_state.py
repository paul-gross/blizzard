"""The fact-egress export's state vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from typing import Literal

EgressState = Literal["on", "off", "rejected"]
