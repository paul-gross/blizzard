"""Trusted reverse-proxy forwarded-header resolution (issue #130).

``X-Forwarded-Proto`` / ``X-Forwarded-For`` are honored **only** when the direct peer is a configured
trusted proxy, so an untrusted client cannot forge either; an empty registry (the default) resolves both
to their direct-connection values. Cross-cutting infrastructure with no domain rules
(``bzh:domain-core``), hence :mod:`blizzard.foundation`."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

_XFF_HEADER = "x-forwarded-for"
_XFP_HEADER = "x-forwarded-proto"


@dataclass(frozen=True)
class TrustedProxies:
    """The configured reverse-proxy trust set. ``networks`` is empty by default, in which case every
    resolution returns its direct-connection input unchanged."""

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
        """Whether ``host`` is one of the configured trusted proxies. A ``None`` peer or a non-IP
        token matches nothing."""
        if host is None or not self.networks:
            return False
        try:
            addr = ip_address(host)
        except ValueError:
            return False
        return any(addr in network for network in self.networks)

    def effective_scheme(self, *, direct_scheme: str, peer: str | None, forwarded_proto: str | None) -> str:
        """The effective request scheme: ``X-Forwarded-Proto``'s leftmost hop — the original client's
        scheme — when ``peer`` is a trusted proxy, otherwise ``direct_scheme``."""
        if not self._trusts(peer) or not forwarded_proto:
            return direct_scheme
        first_hop = forwarded_proto.split(",")[0].strip().lower()
        return first_hop or direct_scheme

    def effective_client_ip(self, *, direct_peer: str, forwarded_for: str | None) -> str:
        """The effective client IP: behind a trusted ``direct_peer``, the **rightmost untrusted hop**
        of ``X-Forwarded-For`` — the leftmost entry is forgeable, the first hop we do not control is
        not. Otherwise ``direct_peer`` stands (pinned by
        tests/test_forwarded.py::test_client_ip_stops_at_the_first_untrusted_hop_from_the_right)."""
        if not self._trusts(direct_peer) or not forwarded_for:
            return direct_peer
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        for hop in reversed(hops):
            if not self._trusts(hop):
                return hop
        return direct_peer
