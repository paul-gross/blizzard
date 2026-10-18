"""The flags answer — ``GET /api/flags`` on the hub and on a host: every live flag the process answers for and whether
it is on, and the ones its ``[flags]`` table changed."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel


class FlagsResponse(BaseModel):
    """An open map, so adding or retiring a flag never changes the OpenAPI document."""

    flags: dict[str, bool]
    overridden: list[str]


class FlagOff(BaseModel):
    """The ``detail`` of a ``404``: the route, field, command or option is held behind a flag that is off. ``flag``
    names the flag; ``message`` says so in the operator's words."""

    code: Literal["flag_off"] = "flag_off"
    flag: str
    message: str

    @classmethod
    def of(cls, flag: str) -> Self:
        """The refusal for ``flag``, with the one message text every surface shows."""
        return cls(flag=flag, message=f"not available yet: it is behind the flag {flag}, which is off")


class FlagOffResponse(BaseModel):
    """The body of a ``404`` from a route a flag holds: the refusal, under ``detail``."""

    detail: FlagOff
