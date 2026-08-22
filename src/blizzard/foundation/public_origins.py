"""The browser-reachable origins a runner declares for its SSO federation callback (issue #287).

A ``redirect_uri`` instructs the hub to POST an identity token to an address, so the acceptable set is
declared and registered up front, never derived from a request. Placed here to mirror
:mod:`blizzard.foundation.forwarded`'s ``TrustedProxies`` — a config value object validated at load and
carried verbatim; `blizzard/docs/deployment/human-auth.md` §Runner-side federation owns the operator procedure."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})
#: Ports a browser omits from ``Host``, so a declared origin naming one still matches.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _authority(url: str) -> str | None:
    """The browser-visible ``host:port`` a URL reduces to, lowercased and without the scheme's default
    port. Validation, duplicate detection and selection all compare this one normal form, so they cannot
    disagree about whether two spellings name one origin. ``None`` when there is no usable authority."""
    split = urlsplit(url)
    try:
        port = split.port
    except ValueError:
        return None
    host = (split.hostname or "").lower()
    if not host:
        return None
    if port is None or port == _DEFAULT_PORTS.get(split.scheme.lower()):
        return host
    return f"{host}:{port}"


@dataclass(frozen=True)
class PublicOrigins:
    """Every base URL a runner answers on, in declaration order; the first is its canonical one.

    Entries arrive already stripped of a trailing slash (:meth:`entries`), so one origin cannot register
    as two and a derived callback URI is a plain concatenation."""

    urls: tuple[str, ...] = ()

    @classmethod
    def entries(cls, raw: object, invalid: type[Exception]) -> tuple[str, ...]:
        """Authored entries, validated then carried verbatim so they round-trip to toml; a bad one fails
        here rather than as an opaque ``unregistered redirect_uri`` from the hub later. ``invalid`` is the
        caller's config-error type. A lone origin may be a bare string, and an empty entry is *absent*
        rather than malformed — an empty ``public_url`` declares no federation identity."""
        if raw is None:
            return ()
        declared = (raw,) if isinstance(raw, str) else raw
        if not isinstance(declared, (list, tuple)):
            raise invalid(f"public_url must be a URL or a list of URLs, got {type(raw).__name__}")
        entries = tuple(stripped.rstrip("/") for entry in declared if (stripped := str(entry).strip()))
        seen: dict[str, str] = {}
        for entry in entries:
            split = urlsplit(entry)
            if split.scheme.lower() not in _ALLOWED_SCHEMES:
                raise invalid(f"public URL must be an http(s) URL including a host, got {entry!r}")
            if split.path or split.query or split.fragment:
                raise invalid(f"public URL must be a bare origin with no path, query, or fragment, got {entry!r}")
            if "@" in split.netloc:
                raise invalid(f"public URL must carry no userinfo — it is registered with the hub, got {entry!r}")
            authority = _authority(entry)
            if authority is None:
                raise invalid(f"public URL must be an http(s) URL including a host, got {entry!r}")
            if authority in seen:
                # Selection matches on `Host`, which carries neither scheme nor a default port, so two
                # such spellings are indistinguishable at request time rather than merely redundant.
                raise invalid(f"public URLs {seen[authority]!r} and {entry!r} are the same origin to a browser")
            seen[authority] = entry
        return entries

    @classmethod
    def of(cls, *urls: str) -> PublicOrigins:
        """The declared set, in declaration order. Empty entries drop out, and a repeat of an authority
        already declared is discarded rather than refused, so the first declaration stays canonical."""
        kept: dict[str, str] = {}
        for url in urls:
            if url and (authority := _authority(url)) is not None:
                kept.setdefault(authority, url.rstrip("/"))
        return cls(tuple(kept.values()))

    @property
    def canonical(self) -> str | None:
        """The first declared origin — the fallback when a request matches none."""
        return self.urls[0] if self.urls else None

    def callback_uris(self, path: str) -> tuple[str, ...]:
        """One callback URI per declared origin: exactly the set the hub registers and exact-matches."""
        return tuple(f"{url}{path}" for url in self.urls)

    def select(self, host: str | None) -> str | None:
        """The declared origin whose authority is ``host``, or ``None`` when none is. A membership test,
        never a construction: the scheme comes from the declared entry, so a TLS-terminating proxy
        forwarding cleartext cannot downgrade a declared ``https`` origin."""
        if not host:
            return None
        raw = host.strip().lower()
        for url in self.urls:
            scheme = urlsplit(url).scheme.lower()
            if (declared := _authority(url)) is not None and declared == _authority(f"{scheme}://{raw}"):
                return url
        return None
