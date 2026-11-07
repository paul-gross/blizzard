"""The one reading of a refusal body a client shows its operator.

Free of pydantic, so a CLI command that never builds a wire model does not pay for importing one.
"""

from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.roles import dto


@dto
@dataclass(frozen=True)
class ProblemReading:
    """What a client reads from a refusal body: its ``type`` (``None`` when absent), the message to show the operator,
    and — only for a body that predates problem details — the ``detail.code`` it carried."""

    type: str | None
    message: str | None
    legacy_code: str | None
    detail: Any
    title: str | None


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
    """The one reading of a decoded refusal body. The message is a string ``detail`` as it is, a legacy object
    ``detail``'s ``message``, a validation list joined, else the ``title``; ``None`` when the body gives none."""
    body = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
    kind = body.get("type")
    title = body.get("title")
    detail = body.get("detail")
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
        detail=detail,
        title=title if isinstance(title, str) else None,
    )
