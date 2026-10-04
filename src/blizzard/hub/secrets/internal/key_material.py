"""Key-generation identity shared by every key provider binding."""

from __future__ import annotations

import hashlib

from blizzard.hub.domain.config.secrets import KeyGeneration

KEY_BYTES = 32
_KEY_ID_DOMAIN = b"blizzard.hub.secret-key-id/v1\x00"
_KEY_ID_HEX_CHARS = 24


def generation_of(material: bytes) -> KeyGeneration:
    """``key_id`` is a domain-separated SHA-256 fingerprint of the material, so the same
    key reads as the same generation whichever source holds it."""
    key_id = hashlib.sha256(_KEY_ID_DOMAIN + material).hexdigest()[:_KEY_ID_HEX_CHARS]
    return KeyGeneration(key_id=key_id, material=material)
