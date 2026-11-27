"""Normalization of a transition's optional explanation."""

TRANSITION_REASON_MAX_LENGTH = 2000


def normalize_transition_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return reason.strip()[:TRANSITION_REASON_MAX_LENGTH] or None
