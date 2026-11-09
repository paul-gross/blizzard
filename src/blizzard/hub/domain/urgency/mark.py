"""An urgency's mark: the emoji or the icon its pill shows, validated so a word cannot pass for a mark and an image
the board draws can never carry script.

An emoji is 1 to 16 code points with no whitespace, control character, or ASCII letter; an ASCII digit, ``#`` or ``*``
stands only as the base of a keycap sequence. An icon is PNG or WebP by its bytes' signature, at most 32 KiB decoded;
SVG is refused outright. Its digest — the hex SHA-256 of its bytes — is what every view carries instead of the bytes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.errors import ConfigFieldError

#: The most code points an emoji mark may hold — room for a ZWJ family or a flag with its modifiers.
EMOJI_MAX_CODE_POINTS = 16

#: The most bytes an icon may hold once decoded.
ICON_MAX_BYTES = 32 * 1024

_KEYCAP_BASES = frozenset("0123456789#*")
_KEYCAP = "⃣"
_VARIATION_SELECTOR_16 = "️"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SVG_MEDIA_TYPE = "image/svg+xml"


class IconMediaType(StrEnum):
    """The image types an icon may be."""

    PNG = "image/png"
    WEBP = "image/webp"


class UrgencyMarkConflict(ConfigFieldError):
    """A write would leave an urgency carrying both an emoji and an icon."""

    code = "urgency_mark_conflict"

    def __init__(self) -> None:
        super().__init__(
            "icon", "an urgency carries an emoji or an icon, not both; clear one with null to set the other"
        )


@domain_model
@dataclass(frozen=True)
class IconMark:
    """What a view carries of an icon: its media type and the digest of its bytes, never the bytes."""

    media_type: IconMediaType
    digest: str


@domain_model
@dataclass(frozen=True)
class Icon:
    """An icon's validated bytes and their media type."""

    media_type: IconMediaType
    data: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def mark(self) -> IconMark:
        return IconMark(self.media_type, self.digest)

    @property
    def encoded(self) -> str:
        """The bytes as base64 — the form the config document carries."""
        return base64.b64encode(self.data).decode("ascii")


@domain_model
@dataclass(frozen=True)
class IconShape:
    """An icon as a request or a document states it: a media type and base64 bytes."""

    media_type: str
    data: str


def parse_emoji(raw: object) -> str:
    """``raw`` when it is an emoji mark; :class:`ConfigFieldError` on ``emoji``, naming the reason, otherwise."""
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigFieldError("emoji", "must not be blank")
    if len(raw) > EMOJI_MAX_CODE_POINTS:
        raise ConfigFieldError("emoji", f"must be at most {EMOJI_MAX_CODE_POINTS} code points, got {len(raw)}")
    for index, char in enumerate(raw):
        if char.isspace():
            raise ConfigFieldError("emoji", "must not carry whitespace")
        if unicodedata.category(char) == "Cc":
            raise ConfigFieldError("emoji", "must not carry a control character")
        if char.isascii() and char.isalpha():
            raise ConfigFieldError("emoji", f"must not carry a letter, got {raw!r}")
        if char in _KEYCAP_BASES and not _keycap_at(raw, index):
            raise ConfigFieldError("emoji", f"a digit, # or * stands only as a keycap's base, got {raw!r}")
    return raw


def _keycap_at(raw: str, index: int) -> bool:
    """Whether the keycap base at ``index`` is followed by the keycap, an emoji variation selector between them."""
    rest = raw[index + 1 :]
    if rest.startswith(_VARIATION_SELECTOR_16):
        rest = rest[1:]
    return rest.startswith(_KEYCAP)


def _sniffed(data: bytes) -> IconMediaType | None:
    if data.startswith(_PNG_SIGNATURE):
        return IconMediaType.PNG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IconMediaType.WEBP
    return None


def parse_icon(shape: object) -> Icon:
    """The icon ``shape`` states; :class:`ConfigFieldError` on ``icon`` for SVG, bytes that are neither PNG nor WebP by
    their signature or that are not the media type stated, bytes over 32 KiB decoded, or data that is not base64."""
    if not isinstance(shape, IconShape):
        raise ConfigFieldError("icon", "must state a media_type and base64 data")
    if shape.media_type.strip().lower() == _SVG_MEDIA_TYPE:
        raise ConfigFieldError("icon", "SVG is refused; an icon is PNG or WebP")
    try:
        data = base64.b64decode(shape.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigFieldError("icon", "data must be base64") from exc
    if len(data) > ICON_MAX_BYTES:
        raise ConfigFieldError("icon", f"must be at most {ICON_MAX_BYTES} bytes decoded, got {len(data)}")
    sniffed = _sniffed(data)
    if sniffed is None:
        raise ConfigFieldError("icon", "must be a PNG or WebP image by its bytes")
    if shape.media_type != sniffed.value:
        raise ConfigFieldError("icon", f"its bytes are {sniffed.value}, not {shape.media_type!r}")
    return Icon(sniffed, data)


def require_one_mark(emoji: str | None, icon: IconMark | None) -> None:
    """:class:`UrgencyMarkConflict` when both marks are set."""
    if emoji is not None and icon is not None:
        raise UrgencyMarkConflict()
