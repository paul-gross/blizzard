"""The answer a delivering action gives from its record's result."""

from __future__ import annotations

from blizzard.hub.domain.actions.effects import EffectResult, EffectStatus, IEffectSink, Marker, Pass


async def answer_delivery(
    result: EffectResult,
    effects: IEffectSink,
    *,
    failure_marker_name: str,
    action: str,
    invalid_choice: str = "invalid",
) -> Pass:
    """``recorded``; ``invalid_choice`` (``invalid`` by default) once the recorder's explanation is durable as
    ``failure_marker_name``; or ``failure`` for a fenced record. ``action`` names the delivery in the log."""
    match result.status:
        case EffectStatus.RECORDED:
            return Pass(choice="recorded")
        case EffectStatus.INVALID:
            await effects.record(Marker(failure_marker_name, result.detail))
            return Pass(choice=invalid_choice, log=(f"{action} rejected: {result.detail}",))
        case EffectStatus.FENCED:
            return Pass(choice="failure", log=(f"{action} not recorded: {result.detail}",))
