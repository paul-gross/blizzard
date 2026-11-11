"""Selftest status vocabulary."""

from typing import Literal

#: A concluded selftest run's status — the only statuses ever recorded; a still-``"running"`` run never is.
SelfTestTerminalStatus = Literal["passed", "failed"]


#: Where a selftest run stands: ``running`` until it concludes ``passed`` or ``failed``.
type SelfTestStatus = Literal["running", SelfTestTerminalStatus]
