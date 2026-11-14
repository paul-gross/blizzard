"""What a credential binding's setting accepts."""

from __future__ import annotations

from enum import StrEnum

from blizzard.foundation.component_name import ComponentNamed


class SettingKind(ComponentNamed, StrEnum):
    """What a binding's setting takes: one of its choices, or a positive integer."""

    __component_name__ = "CredentialSettingKind"

    CHOICE = "choice"
    POSITIVE_INTEGER = "positive-integer"
