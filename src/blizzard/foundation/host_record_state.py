"""The runner host's workspace and subscription record state vocabulary — one definition, shared by the host's wire
and its API."""

from __future__ import annotations

from typing import Literal

#: Whether a host's workspace or subscription record is in force, or retired.
HostRecordState = Literal["active", "retired"]
