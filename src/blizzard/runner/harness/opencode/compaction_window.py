"""Map a usable window to OpenCode's model input limit and effective reserve."""

from collections.abc import Mapping

from blizzard.runner.harness.json_value import JsonObject, JsonValue
from blizzard.runner.harness.opencode.model_limits import OpenCodeModelLimits
from blizzard.runner.harness.opencode.permissions.permission_compose import merge_overrides


def _object(value: JsonValue | None) -> Mapping[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def compose_window(
    base: JsonObject,
    effective: Mapping[str, JsonValue],
    provider: str,
    model: str,
    window: str,
    limits: OpenCodeModelLimits,
) -> JsonObject | None:
    """Set input to window plus reserve, leaving the request's output cap untouched."""
    compaction = _object(effective.get("compaction"))
    if effective.get("compaction") is not None and compaction is None:
        return None
    reserved = compaction.get("reserved") if compaction else None
    node: Mapping[str, JsonValue] = effective
    for key in ("provider", provider, "models", model, "limit"):
        value = node.get(key)
        if value is None:
            node = {}
            break
        child = _object(value)
        if child is None:
            return None
        node = child
    limit = node
    context = limit.get("context", limits.context)
    output = limit.get("output", limits.output)
    if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
        return None
    if not isinstance(output, int) or isinstance(output, bool) or output <= 0:
        return None
    if reserved is None:
        reserved = min(20000, min(output, 32000))
    if not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < 0:
        return None
    numeric = int(window[:-1]) * 1000 if window.endswith(("k", "K")) else int(window)
    return merge_overrides(
        base,
        {
            "provider": {
                provider: {
                    "models": {model: {"limit": {"context": context, "output": output, "input": numeric + reserved}}}
                }
            }
        },
    )
