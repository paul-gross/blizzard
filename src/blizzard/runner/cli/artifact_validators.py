"""Submit-time validation of format-backed assets: ``artifact create`` looks the asset's name up
here and refuses a malformed body in the worker's own turn, rather than at the hub's later parse.
An asset with no registered validator is stored as given."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel, ValidationError


def _review_finding_delta() -> type[BaseModel]:
    # Imported on use: every worker CLI call loads this module, and only a submission of this asset needs the model.
    from blizzard.wire.finding import ReviewFindingDelta

    return ReviewFindingDelta


_FORMATS: dict[str, tuple[Callable[[], type[BaseModel]], str]] = {
    "review-finding-delta": (_review_finding_delta, "review/finding-format"),
}


def validate_artifact_content(name: str, content: str) -> str | None:
    """The field-level reason ``content`` is not a valid ``name`` asset, or ``None`` when it is
    valid or ``name`` has no registered format."""
    registered = _FORMATS.get(name)
    if registered is None:
        return None
    from pydantic import ValidationError

    load_model, system_artifact = registered
    try:
        load_model().model_validate_json(content)
    except ValidationError as exc:
        return (
            f"{name!r} does not match its format: {_summarize(exc)} "
            f"(full shape: `blizzard runner artifact get --scope system {system_artifact} --content`)"
        )
    return None


def _summarize(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
