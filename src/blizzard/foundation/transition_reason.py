"""Normalization of a transition's optional explanation."""

TRANSITION_REASON_MAX_LENGTH = 2000


def normalize_transition_reason(reason: str | None, *, truncated: bool = False) -> str | None:
    """A valid clipped prefix may end in meaningful whitespace, unlike an untrimmed raw reason."""
    if reason is None:
        return None
    if truncated and len(reason) == TRANSITION_REASON_MAX_LENGTH and reason == reason.lstrip():
        return reason if reason.strip() else None
    return reason.strip()[:TRANSITION_REASON_MAX_LENGTH] or None
