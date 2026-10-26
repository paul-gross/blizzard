"""The forge kinds a repository's forge may declare, and what each kind offers."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from enum import StrEnum

from blizzard.hub.domain.config.capabilities import Capability

#: The only forge kinds a forge may declare; an unknown kind fails at config load, not at first use.
KNOWN_FORGE_KINDS = frozenset({"github"})

#: What each forge kind's adapter offers.
FORGE_KIND_CAPABILITIES: Mapping[str, frozenset[Capability]] = {
    "github": frozenset(Capability),
}


class OriginRefusal(StrEnum):
    """Why an address is not an origin. The spellings are pinned by ``tests/fixtures/canonical_origin_cases.json``,
    which the migration's frozen copy of the rule is tested against."""

    #: Not an origin at all: no scheme or a scheme other than http(s), no host, a malformed port or host, or a space.
    NOT_AN_ORIGIN = "not-an-origin"
    #: An origin with a user, password, query, or fragment on it, which the rule refuses rather than strips.
    NEEDS_CLEANING = "needs-cleaning"


class OriginRefused(ValueError):
    """An address :func:`canonical_origin` refuses, with the kind of refusal and the reason it gives."""

    def __init__(self, kind: OriginRefusal, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


_DEFAULT_PORTS = {"http": "80", "https": "443"}
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*)://(.*)", re.DOTALL)
_HOST_CHARS = re.compile(r"[a-z0-9._-]+")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def canonical_origin(url: str) -> str:
    """The one form a forge address is compared in; :class:`OriginRefused` when it is not one.

    The rule is pinned case by case in ``tests/fixtures/canonical_origin_cases.json``: ``http(s)`` scheme and host
    lowercased, IDNA 2003 host, default port dropped, path escapes normalized and trailing slashes stripped."""
    text = url.strip()
    if re.search(r"\s", text):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has whitespace inside it")
    matched = _SCHEME.fullmatch(text)
    if matched is None:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has no scheme; an origin starts with http:// or https://")
    scheme = matched.group(1).lower()
    if scheme not in _DEFAULT_PORTS:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"its scheme is {scheme}, not http or https")
    rest = matched.group(2)
    cut = min((i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest))
    authority, remainder = rest[:cut], rest[cut:]
    if "@" in authority:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a user or password")
    if "?" in remainder:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a query")
    if "#" in remainder:
        raise OriginRefused(OriginRefusal.NEEDS_CLEANING, "it carries a fragment")
    host, port = _host_and_port(authority)
    kept_port = "" if port in ("", _DEFAULT_PORTS[scheme]) else f":{port}"
    return f"{scheme}://{host}{kept_port}{_canonical_path(remainder)}"


def _host_and_port(authority: str) -> tuple[str, str]:
    """The canonical host and the port digits (normalized, possibly empty) of an authority."""
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its IPv6 literal is not closed")
        literal, tail = authority[1:end], authority[end + 1 :]
        if "%" in literal:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its IPv6 literal carries a percent-escape or zone")
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"[{literal}] is not an IPv6 address") from None
        if tail and not tail.startswith(":"):
            raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has text after its IPv6 literal")
        return f"[{literal.lower()}]", _port(tail[1:])
    host, _, port = authority.partition(":")
    if ":" in port:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its host has more than one colon")
    host = host.lower().removesuffix(".")
    if not host:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "it has no host")
    if "%" in host:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its host carries a percent-escape")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{host} is not a valid host name") from None
    if not _HOST_CHARS.fullmatch(host):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{host} is not a valid host name")
    return host, _port(port)


def _port(digits: str) -> str:
    if not digits:
        return ""
    if not (digits.isascii() and digits.isdigit()) or int(digits) > 65535:
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, f"{digits} is not a port")
    return str(int(digits))


def _canonical_path(path: str) -> str:
    """The path with its escapes normalized (hex uppercased, an escaped unreserved character decoded) and its
    trailing slashes stripped."""

    def normalize(escape: re.Match[str]) -> str:
        char = chr(int(escape.group(1), 16))
        return char if char in _UNRESERVED else f"%{escape.group(1).upper()}"

    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise OriginRefused(OriginRefusal.NOT_AN_ORIGIN, "its path has a malformed percent-escape")
    return _PERCENT_ESCAPE.sub(normalize, path).rstrip("/")
