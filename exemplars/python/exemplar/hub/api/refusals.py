"""The ``HTTPException`` a domain refusal answers with, by status."""

# exemplar: bzh:domain-orchestration-split — a route maps each refusal it meets to its status explicitly.

from __future__ import annotations

from fastapi import HTTPException, status


def not_found(detail: str) -> HTTPException:
    """``404`` — the call names something the tenant does not hold."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def conflict(detail: str) -> HTTPException:
    """``409`` — the current state of what the call acts on refuses it."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def unprocessable(detail: str) -> HTTPException:
    """``422`` — the call's input is well-formed but refused."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
