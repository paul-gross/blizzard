"""The flags answer — ``GET /api/flags`` on the hub and on a host: every live flag the process answers for and whether
it is on, and the ones its ``[flags]`` table changed."""

from __future__ import annotations

from pydantic import BaseModel


class FlagsResponse(BaseModel):
    """An open map, so adding or retiring a flag never changes the OpenAPI document."""

    flags: dict[str, bool]
    overridden: list[str]
