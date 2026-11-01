"""Model limits from OpenCode's worker-local catalog."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonValue, decode_json


@domain_model
@dataclass(frozen=True)
class OpenCodeModelLimits:
    context: int
    output: int
    input: int | None = None


class IOpenCodeModelLimitsCatalog(Protocol):
    def limits_for(self, provider: str, model: str) -> OpenCodeModelLimits | None: ...


def _positive_int(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


class FileOpenCodeModelLimitsCatalog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def limits_for(self, provider: str, model: str) -> OpenCodeModelLimits | None:
        try:
            document = decode_json(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return None
        entry = document.get(provider) if isinstance(document, dict) else None
        models = entry.get("models") if isinstance(entry, dict) else None
        item = models.get(model) if isinstance(models, dict) else None
        limits = item.get("limit") if isinstance(item, dict) else None
        if not isinstance(limits, dict):
            return None
        context, output = _positive_int(limits.get("context")), _positive_int(limits.get("output"))
        input_limit = limits.get("input")
        if context is None or output is None or (input_limit is not None and _positive_int(input_limit) is None):
            return None
        return OpenCodeModelLimits(context, output, _positive_int(input_limit))
