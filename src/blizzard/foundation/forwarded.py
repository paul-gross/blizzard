"""Trusted reverse-proxy forwarded-header resolution (issue #130).

Both daemons derive two security decisions from the direct TCP connection: the session
cookie's ``Secure`` flag (from the request scheme) and the login-throttle / auth-fact
client IP (from the peer address). Both are correct when the daemon is exposed directly
(localhost, tailnet) and wrong behind a TLS-terminating reverse proxy — the proxy speaks
HTTPS to the browser but plain HTTP to the daemon, and every request arrives from the
proxy's own IP.

:class:`TrustedProxies` is the proxy-aware source of truth both daemons consult. It
honors ``X-Forwarded-Proto`` / ``X-Forwarded-For`` **only** when the direct peer is a
configured trusted proxy; a request from any other peer keeps its direct-connection
values regardless of what headers it carries, so honoring a header can never be forged
by an untrusted client. An empty registry (the default) short-circuits both resolutions
to the direct values — byte-identical to the header-blind behavior that predates it.

Cross-cutting infrastructure with no domain rules (``bzh:domain-core``), so it lives
here in :mod:`blizzard.foundation` rather than either daemon's own layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

_XFF_HEADER = "x-forwarded-for"
_XFP_HEADER = "x-forwarded-proto"


@dataclass(frozen=True)
class TrustedProxies:
    """The configured reverse-proxy trust set — parsed once at startup, consulted per
    request. ``networks`` is empty by default, in which case every resolution returns
    its direct-connection input unchanged."""

    networks: tuple[IPv4Network | IPv6Network, ...] = ()

    @classmethod
    def parse(cls, entries: object) -> TrustedProxies:
        """Project raw ``trusted_proxies`` config entries (plain IPs or CIDRs) into
        parsed networks. A single host (``"10.0.0.4"``) becomes a ``/32`` (or ``/128``)
        network; a malformed entry raises :class:`ValueError`, so a bad config fails at
        load rather than silently trusting nothing."""
        if not isinstance(entries, (list, tuple)):
            return cls()
        return cls(tuple(ip_network(str(entry).strip(), strict=False) for entry in entries))

    def _trusts(self, host: str | None) -> bool:
        """Whether ``host`` is one of the configured trusted proxies. A ``None`` peer (a
        unix-socket connection has no ``(host, port)``) or a non-IP token (the ``"unknown"``
        sentinel a caller substitutes for an absent peer) matches nothing."""
        if host is None or not self.networks:
            return False
        try:
            addr = ip_address(host)
        except ValueError:
            return False
        return any(addr in network for network in self.networks)

    def effective_scheme(self, *, direct_scheme: str, peer: str | None, forwarded_proto: str | None) -> str:
        """The scheme the cookie ``Secure`` flag keys on. ``X-Forwarded-Proto`` (its
        leftmost hop — the original client's scheme) decides it only when ``peer`` is a
        trusted proxy; otherwise the daemon's own ``direct_scheme`` stands."""
        if not self._trusts(peer) or not forwarded_proto:
            return direct_scheme
        first_hop = forwarded_proto.split(",")[0].strip().lower()
        return first_hop or direct_scheme

    def effective_client_ip(self, *, direct_peer: str, forwarded_for: str | None) -> str:
        """The client IP the throttle keys on and the auth facts record. When
        ``direct_peer`` is a trusted proxy, the **rightmost untrusted hop** of
        ``X-Forwarded-For`` is the real client — walking right to left past our own
        chained proxies, the first hop we do not control is where a forger could start,
        so it is treated as the client. Otherwise (an untrusted peer, an absent or
        all-trusted header) the direct peer stands, so a forged ``X-Forwarded-For`` from
        an untrusted client cannot dodge the throttle."""
        if not self._trusts(direct_peer) or not forwarded_for:
            return direct_peer
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        for hop in reversed(hops):
            if not self._trusts(hop):
                return hop
        return direct_peer
