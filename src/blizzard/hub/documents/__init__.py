"""The documents the hub ingests, decoded to plain mappings through the config codec seam.

This module is the seam's selection point: it binds the YAML and JSON codecs and answers which one serves a media
type or a path (``bzh:pluggable-seams``). The seam itself — :class:`IConfigCodec` — is
``src/blizzard/hub/documents/codec.py``."""

from __future__ import annotations

from pathlib import PurePath

from blizzard.hub.documents.codec import ConfigDecodeError, IConfigCodec
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

#: Every binding, YAML first.
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
