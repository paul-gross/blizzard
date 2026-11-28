"""The host's disk route — ``GET /api/host/disk``, the free space on the host root's filesystem.

It is a host-root route: its lane resolves it through the host's process scope, never a runner's."""

# exemplar: bzh:dependency-injection, bzh:narrow-seams — a host route names the one service it calls through
# ``FromDishka`` and builds none.

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, HTTPException, status

from blizzard.foundation.instants import iso_utc
from blizzard.wire.base import WireModel
from exemplar.runner.disk.probe import DiskUnreadable
from exemplar.runner.disk.reading import HostDiskReader

router = APIRouter(prefix="/api/host")


class DiskView(WireModel):
    free_bytes: int
    read_at: str


@router.get("/disk", response_model=DiskView, responses={503: {"description": "The filesystem cannot be read."}})
@inject_sync
def read_host_disk(reader: FromDishka[HostDiskReader]) -> DiskView:
    """The free space on the filesystem holding the host's root."""
    try:
        reading = reader.read()
    except DiskUnreadable as unreadable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(unreadable)) from unreadable
    return DiskView(free_bytes=reading.free_bytes, read_at=iso_utc(reading.at))
