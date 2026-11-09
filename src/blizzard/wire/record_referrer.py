"""Wire models for record referrer."""

from blizzard.wire.base import WireModel


class RecordReferrer(WireModel):
    """An enabled record that still names the forge or secret, by kind and key. ``label`` is the record's current slug
    for a slugged kind, whose key is its id; ``None`` for a kind keyed by its name."""

    kind: str
    key: str
    label: str | None = None
