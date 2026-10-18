"""The config codec seam: bytes in, a plain mapping out, and back.

Each binding declares its own media types and file extensions; a consumer asks the registry in
``src/blizzard/hub/documents/__init__.py`` for the binding and never compares a media-type or extension literal
(``bzh:seam-answers-binding-facts``). A binding decodes without validating document-kind fields."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from blizzard.hub.documents.errors import ConfigDecodeError

__all__ = ["ConfigDecodeError", "IConfigCodec"]


class IConfigCodec(Protocol):
    """One document format."""

    @property
    def media_types(self) -> tuple[str, ...]:
        """The media types this binding declares, aliases included."""
        ...

    @property
    def extensions(self) -> tuple[str, ...]:
        """The file extensions this binding declares, each with its leading dot."""
        ...

    def decode(self, data: bytes) -> dict[str, object]:
        """The document's root mapping. Raises :class:`ConfigDecodeError` on malformed syntax, a duplicate
        key, or a root that is not a mapping."""
        ...

    def encode(self, document: Mapping[str, object]) -> bytes:
        """The mapping serialized so that ``decode`` returns it unchanged."""
        ...
