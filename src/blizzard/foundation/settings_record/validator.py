"""The daemon validator's type: the whole candidate entries in, the refused keys out."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.foundation.settings_record.vocabulary import Entry

type Validate = Callable[[Mapping[str, Entry]], Mapping[str, str]]
