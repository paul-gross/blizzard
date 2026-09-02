"""Deterministic secret scrubbing for retained OpenCode compatibility evidence.

Sanitization is a pure value-to-value transform.  It never discovers credentials itself; callers
may provide known sentinel values, while sensitive keys and common bearer/key spellings are
redacted even when a caller did not know the value in advance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

REDACTED = "<redacted>"

_SENSITIVE_KEY_NAMES = frozenset(
    {
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "privatekey",
        "secret",
        "token",
    }
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]\\\"']+)")
_KEY_VALUE_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?:\"|')?[a-z_][a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|authorization|auth|"
    r"password|secret|credential|private[_-]?key|token)(?:\"|')?\s*[=:]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`|[^\s,;&}\]\)]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|credential|key)=)"
    r"([^&#\s\"']+)"
)
_PEM_RE = re.compile(r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----", re.DOTALL)
_STANDALONE_KEY_RE = re.compile(r"(?i)(?<![a-z0-9])(?:sk|rk)-[a-z0-9][a-z0-9_.~+/=-]*")
_PATH_STOP = r"""[^\s"'<>|;&,()\[\]{}]+"""
_FILE_URI_PATH_RE = re.compile(rf"(?P<prefix>\bfile://)(?P<path>(?<!>)/(?!/){_PATH_STOP})", re.IGNORECASE)
_NETWORK_PATH_RE = re.compile(rf"(?<![A-Za-z0-9_./:>\x00-])(?P<path>//{_PATH_STOP})")
_POSIX_PATH_RE = re.compile(rf"(?<![A-Za-z0-9_./>\x00-])(?P<path>/(?!/)(?:{_PATH_STOP}/)*{_PATH_STOP})")
_WINDOWS_PATH_RE = re.compile(rf"(?<![A-Za-z0-9_.-])(?P<path>[A-Za-z]:[\\/]{_PATH_STOP}|\\\\{_PATH_STOP})")
_CLOSING_TAG_RE = re.compile(r"</[A-Za-z][A-Za-z0-9_.:-]*>")


def _key_is_sensitive(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[-.]", "_", key.lower())
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return (
        normalized.endswith("_api_key")
        or normalized.endswith("apikey")
        or normalized == "key"
        or normalized.endswith("_key")
        or normalized.endswith("token")
        or normalized.endswith("secret")
        or normalized.endswith("password")
    )


def _quoted_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        return f"{value[0]}{REDACTED}{value[-1]}"
    return REDACTED


def _redact_assignment(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_quoted_value(match.group('value'))}"


def _redact_query(match: re.Match[str]) -> str:
    return f"{match.group(1)}{REDACTED}"


def _path_placeholder(path: str) -> str:
    normalized = path.rstrip("/\\")
    name = re.split(r"[/\\]", normalized)[-1]
    return f"<host-path>/{name}" if name else "<host-path>"


def _redact_file_uri_path(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_path_placeholder(match.group('path'))}"


def _redact_absolute_path(match: re.Match[str]) -> str:
    return _path_placeholder(match.group("path"))


def is_absolute_host_path(value: str) -> bool:
    return value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.startswith("\\\\")


def _redact_pure_absolute_path(value: str) -> str:
    if is_absolute_host_path(value):
        return _path_placeholder(value)
    return value


def _sanitize_host_paths(value: str) -> str:
    protected_tags: dict[str, str] = {}

    def protect_tag(match: re.Match[str]) -> str:
        marker = f"\x00closing-tag-{len(protected_tags)}\x00"
        protected_tags[marker] = match.group(0)
        return marker

    sanitized = _CLOSING_TAG_RE.sub(protect_tag, value)
    protected_value = sanitized
    sanitized = _redact_pure_absolute_path(sanitized)
    if sanitized != protected_value:
        return _restore_protected_tags(sanitized, protected_tags)
    sanitized = _FILE_URI_PATH_RE.sub(_redact_file_uri_path, sanitized)
    sanitized = _NETWORK_PATH_RE.sub(_redact_absolute_path, sanitized)
    sanitized = _POSIX_PATH_RE.sub(_redact_absolute_path, sanitized)
    sanitized = _WINDOWS_PATH_RE.sub(_redact_absolute_path, sanitized)
    return _restore_protected_tags(sanitized, protected_tags)


def _restore_protected_tags(value: str, protected_tags: Mapping[str, str]) -> str:
    for marker, tag in protected_tags.items():
        value = value.replace(marker, tag)
    return value


def sanitize_text(
    value: str,
    *,
    secrets: Sequence[str] = (),
    path_replacements: Sequence[tuple[str, str]] = (),
) -> str:
    """Scrub known values, credential spellings, and caller-declared path prefixes."""

    return _sanitize_text(value, secrets=secrets, path_replacements=path_replacements, redact_host_paths=True)


def _sanitize_text(
    value: str,
    *,
    secrets: Sequence[str],
    path_replacements: Sequence[tuple[str, str]],
    redact_host_paths: bool,
) -> str:
    """Apply text scrubbing with an explicit choice for host-path redaction."""

    sanitized = value
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        sanitized = sanitized.replace(secret, REDACTED)
    sanitized = _PEM_RE.sub(REDACTED, sanitized)
    sanitized = _BEARER_RE.sub(_redact_bearer, sanitized)
    sanitized = _KEY_VALUE_RE.sub(_redact_assignment, sanitized)
    sanitized = _QUERY_SECRET_RE.sub(_redact_query, sanitized)
    sanitized = _STANDALONE_KEY_RE.sub(REDACTED, sanitized)
    protected_replacements: list[tuple[str, str]] = []
    ordered_replacements = sorted(path_replacements, key=lambda pair: len(pair[0]), reverse=True)
    for index, (source, replacement) in enumerate(ordered_replacements):
        if source:
            marker = f"\x00path-replacement-{index}\x00"
            sanitized = sanitized.replace(source, marker)
            protected_replacements.append((marker, replacement))
    if redact_host_paths:
        sanitized = _sanitize_host_paths(sanitized)
    for marker, replacement in protected_replacements:
        sanitized = sanitized.replace(marker, replacement)
    return sanitized


def _redact_bearer(match: re.Match[str]) -> str:
    value = match.group(0)
    prefix = match.group(1)
    token = value[len(prefix) :]
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        token = f"{token[0]}{REDACTED}{token[-1]}"
    else:
        token = REDACTED
    return f"{prefix}{token}"


def sanitize_value(
    value: object,
    *,
    secrets: Sequence[str] = (),
    path_replacements: Sequence[tuple[str, str]] = (),
) -> object:
    """Return a recursively sanitized copy of a JSON-compatible value."""

    if isinstance(value, Mapping):
        sanitized: dict[object, object] = {}
        for key, nested in value.items():
            sanitized_key = (
                sanitize_text(key, secrets=secrets, path_replacements=path_replacements)
                if isinstance(key, str)
                else key
            )
            if _key_is_sensitive(key):
                sanitized[sanitized_key] = REDACTED
            elif key == "http" and isinstance(nested, Mapping):
                sanitized[sanitized_key] = _sanitize_http_mapping(
                    nested,
                    secrets=secrets,
                    path_replacements=path_replacements,
                )
            else:
                sanitized[sanitized_key] = sanitize_value(nested, secrets=secrets, path_replacements=path_replacements)
        return sanitized
    if isinstance(value, list):
        return [sanitize_value(item, secrets=secrets, path_replacements=path_replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(item, secrets=secrets, path_replacements=path_replacements) for item in value)
    if isinstance(value, str):
        return sanitize_text(value, secrets=secrets, path_replacements=path_replacements)
    return value


def _sanitize_http_mapping(
    value: Mapping[object, object],
    *,
    secrets: Sequence[str],
    path_replacements: Sequence[tuple[str, str]],
) -> dict[object, object]:
    sanitized: dict[object, object] = {}
    for key, nested in value.items():
        sanitized_key = (
            sanitize_text(key, secrets=secrets, path_replacements=path_replacements) if isinstance(key, str) else key
        )
        if key == "path" and isinstance(nested, str):
            sanitized[sanitized_key] = _sanitize_text(
                nested,
                secrets=secrets,
                path_replacements=path_replacements,
                redact_host_paths=False,
            )
        else:
            sanitized[sanitized_key] = sanitize_value(nested, secrets=secrets, path_replacements=path_replacements)
    return sanitized


def sanitize_json(
    value: object,
    *,
    secrets: Sequence[str] = (),
    path_replacements: Sequence[tuple[str, str]] = (),
) -> str:
    """Serialize a sanitized value in a stable form suitable for committed evidence."""

    import json

    cleaned = sanitize_value(value, secrets=secrets, path_replacements=path_replacements)
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class OpenCodeSanitizer:
    """A reusable pure sanitizer carrying the evidence-specific replacements."""

    secrets: tuple[str, ...] = ()
    path_replacements: tuple[tuple[str, str], ...] = ()

    def value(self, value: object) -> object:
        return sanitize_value(value, secrets=self.secrets, path_replacements=self.path_replacements)


__all__ = [
    "REDACTED",
    "OpenCodeSanitizer",
    "is_absolute_host_path",
    "sanitize_json",
    "sanitize_text",
    "sanitize_value",
]
