"""The config codec seam: bytes in, a plain mapping out, and back.

Each binding declares its own media types and file extensions; a consumer asks the registry for the binding
and never compares a media-type or extension literal (``bzh:seam-answers-binding-facts``). No module outside
``src/blizzard/hub/documents/internal/yaml_codec.py`` imports PyYAML (``bzh:config-codec``). A binding decodes and
never validates — the kind's one validator reports a wrong-typed field, so every format yields the same message."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath
from typing import Protocol

from blizzard.hub.documents.errors import ConfigDecodeError
from blizzard.hub.documents.internal.json_codec import JSON_CODEC
from blizzard.hub.documents.internal.yaml_codec import YAML_CODEC

__all__ = [
    "CONFIG_CODECS",
    "YAML_CODEC",
    "ConfigDecodeError",
    "IConfigCodec",
    "accepted_extensions",
    "codec_for_media_type",
    "codec_for_path",
]


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


#: Every binding, YAML first. Pure and stateless, so a constant rather than an injected collaborator.
CONFIG_CODECS: tuple[IConfigCodec, ...] = (YAML_CODEC, JSON_CODEC)


def codec_for_media_type(media_type: str) -> IConfigCodec | None:
    """The binding declaring ``media_type`` (parameters such as ``; charset=`` ignored), or ``None``."""
    bare = media_type.split(";", 1)[0].strip().lower()
    return next((codec for codec in CONFIG_CODECS if bare in codec.media_types), None)


def codec_for_path(path: str | PurePath) -> IConfigCodec | None:
    """The binding declaring ``path``'s extension (case-insensitive), or ``None``."""
    suffix = PurePath(path).suffix.lower()
    return next((codec for codec in CONFIG_CODECS if suffix in codec.extensions), None)


def accepted_extensions() -> tuple[str, ...]:
    """Every extension some binding declares, in registry order."""
    return tuple(extension for codec in CONFIG_CODECS for extension in codec.extensions)
