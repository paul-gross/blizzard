"""The outcomes of applying a configuration entry."""

from typing import Literal

ConfigApplyOp = Literal["create", "edit", "retire", "enable", "replace", "unchanged"]
