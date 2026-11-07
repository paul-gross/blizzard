"""The one reading of a refusal body a client shows its operator.

Reads a decoded body with plain types, so the reading itself builds no wire model.
"""

from dataclasses import dataclass
from typing import cast

from blizzard.foundation.roles import dto


@dto
@dataclass(frozen=True)
class ProblemReading:
    """What a client reads from a refusal body: its ``type`` (``None`` when absent), the message to show the operator,
    the ``detail.code`` an object ``detail`` carried (a pre-problem-details body keys on it), and the body's members
    (``members``) for a verb that shows a typed refusal's extension fields."""

    type: str | None
    message: str | None
    legacy_code: str | None
    detail: object
    title: str | None
    members: dict[str, object]


def is_kind(reading: ProblemReading, kind: str, legacy_code: str | None = None) -> bool:
    """Whether ``reading`` is the ``kind`` refusal — by its ``type``, or, for a body that predates problem details and
    carries none, by the ``legacy_code`` it keyed on."""
    return reading.type == kind or (
        reading.type is None and legacy_code is not None and reading.legacy_code == legacy_code
    )


def _validation_message(entries: list[object]) -> str | None:
    parts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = cast(dict[str, object], entry)
        loc = item.get("loc")
        if isinstance(loc, list) and loc and "msg" in item:
            parts.append(f"{cast(list[object], loc)[-1]}: {item['msg']}")
    return "; ".join(parts) or None


def read_problem(payload: object) -> ProblemReading:
    """The one reading of a decoded refusal body. ``legacy_code`` is the ``code`` of any object ``detail``, typed or
    not; read it only where ``type`` is ``None``. The message is a string ``detail`` as it is, a legacy object
    ``detail``'s ``message``, a validation list joined, else the ``title``; ``None`` when the body gives none."""
    body = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    kind = body.get("type")
    title = body.get("title")
    detail: object = body.get("detail")
    message: str | None = None
    legacy_code: str | None = None
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, dict):
        fields = cast(dict[str, object], detail)
        if isinstance(fields.get("message"), str):
            message = cast(str, fields["message"])
        if isinstance(fields.get("code"), str):
            legacy_code = cast(str, fields["code"])
    elif isinstance(detail, list):
        message = _validation_message(cast(list[object], detail))
    if message is None and isinstance(title, str):
        message = title
    return ProblemReading(
        type=kind if isinstance(kind, str) else None,
        message=message,
        legacy_code=legacy_code,
        detail=body.get("detail"),
        title=title if isinstance(title, str) else None,
        members=body,
    )
