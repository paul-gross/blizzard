"""The vocabulary of a credential's read-only check: what each operation ended in, and what kind of record a use is."""

from __future__ import annotations

from enum import StrEnum

from blizzard.foundation.component_name import ComponentNamed


class CheckOutcome(ComponentNamed, StrEnum):
    """What one operation of a credential's read-only check ended in."""

    __component_name__ = "CredentialCheckOutcome"

    #: A read landing or ingestion makes answered 2xx.
    PROVEN = "proven"
    #: A write the credential holds a readable grant for; not exercised.
    GRANTED = "granted"
    #: A write no readable grant covers — a fine-grained token exposes none; not exercised.
    UNPROVEN = "unproven"
    #: The forge refused the call; its diagnostic says why.
    REFUSED = "refused"
    #: Outside what the credential's authorization offers; no call made.
    UNSUPPORTED = "unsupported"


class UseKind(ComponentNamed, StrEnum):
    """The kind of record a credential check checks the credential against."""

    __component_name__ = "CredentialUseKind"

    REPOSITORY = "repository"
    WORK_SOURCE = "work_source"
