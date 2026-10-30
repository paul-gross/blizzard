"""Submit-time validation of format-backed assets: ``artifact create`` looks the asset's name up
here and refuses a malformed body in the worker's own turn, rather than at the hub's later parse.
An asset with no registered validator is stored as given."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from blizzard.wire.finding import ReviewFindingDelta

_FORMATS: dict[str, tuple[type[BaseModel], str]] = {
    "review-finding-delta": (ReviewFindingDelta, "review/finding-format"),
}


def validate_artifact_content(name: str, content: str) -> str | None:
    """The field-level reason ``content`` is not a valid ``name`` asset, or ``None`` when it is
    valid or ``name`` has no registered format."""
    registered = _FORMATS.get(name)
    if registered is None:
        return None
    model, system_artifact = registered
    try:
        model.model_validate_json(content)
    except ValidationError as exc:
        return (
            f"{name!r} does not match its format: {_summarize(exc)} "
            f"(full shape: `blizzard runner artifact get --scope system {system_artifact} --content`)"
        )
    return None


def _summarize(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
