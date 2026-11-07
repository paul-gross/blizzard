"""Reviewed fingerprints of HTTP-shaped calls on unannotated injected receivers.

An entry is a file-relative path mapped to a count and a SHA-256 digest of
normalized enclosing ASTs and calls. Review the calls and their provenance
before updating; an inventory entry is not proof of a non-HTTP receiver.
"""

FINGERPRINTS: dict[str, tuple[int, str]] = {}
