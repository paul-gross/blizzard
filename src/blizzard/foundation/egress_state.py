"""The fact-egress export's state vocabulary, shared by the hub's status read and the operator wire body."""

from __future__ import annotations

from typing import Literal

EgressState = Literal["on", "off", "rejected"]
