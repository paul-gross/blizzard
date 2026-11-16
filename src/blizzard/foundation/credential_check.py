"""What one operation of a credential's read-only check ended in."""

from __future__ import annotations

from typing import Literal

#: What one operation of a credential's read-only check ended in.
type CredentialCheckOutcome = Literal["proven", "granted", "unproven", "refused", "unsupported"]
