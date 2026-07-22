"""The trusted reverse-proxy forwarded-header resolver (unit tier, issue #130).

Pure resolution logic — trust matching, the rightmost-untrusted-hop walk, scheme
selection, and the empty-set passthrough that keeps a direct-exposure daemon
byte-identical to its header-blind predecessor.
"""

from __future__ import annotations

import pytest

from blizzard.foundation.forwarded import TrustedProxies

pytestmark = pytest.mark.unit


# --- parsing ----------------------------------------------------------------------


def test_parse_accepts_plain_ips_and_cidrs() -> None:
    trusted = TrustedProxies.parse(["10.0.0.4", "192.168.0.0/16", "2001:db8::/32"])
    assert len(trusted.networks) == 3


def test_parse_empty_is_the_default_empty_set() -> None:
    assert TrustedProxies.parse([]) == TrustedProxies()
    assert TrustedProxies.parse(()) == TrustedProxies()


def test_parse_a_bare_host_becomes_a_single_address_network() -> None:
    trusted = TrustedProxies.parse(["10.0.0.4"])
    # /32 — matches exactly that host and nothing adjacent.
    assert trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="1.2.3.4") == "1.2.3.4"
    assert trusted.effective_client_ip(direct_peer="10.0.0.5", forwarded_for="1.2.3.4") == "10.0.0.5"


def test_parse_rejects_a_malformed_entry() -> None:
    with pytest.raises(ValueError):
        TrustedProxies.parse(["not-an-ip"])


def test_parse_of_a_non_sequence_is_the_empty_set() -> None:
    assert TrustedProxies.parse(None) == TrustedProxies()


# --- the empty set is a pure passthrough ------------------------------------------


def test_empty_set_ignores_forwarded_headers_entirely() -> None:
    trusted = TrustedProxies()
    assert (
        trusted.effective_scheme(direct_scheme="http", peer="10.0.0.4", forwarded_proto="https") == "http"
    )
    assert trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="1.2.3.4") == "10.0.0.4"


# --- effective scheme -------------------------------------------------------------


def test_scheme_honors_forwarded_proto_from_a_trusted_proxy() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_scheme(direct_scheme="http", peer="10.0.0.4", forwarded_proto="https") == "https"


def test_scheme_ignores_forwarded_proto_from_an_untrusted_peer() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_scheme(direct_scheme="http", peer="203.0.113.9", forwarded_proto="https") == "http"


def test_scheme_takes_the_leftmost_hop_of_a_chained_forwarded_proto() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_scheme(direct_scheme="http", peer="10.0.0.4", forwarded_proto="https, http") == "https"


def test_scheme_falls_back_to_direct_when_header_absent_or_a_peerless_connection() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_scheme(direct_scheme="http", peer="10.0.0.4", forwarded_proto=None) == "http"
    assert trusted.effective_scheme(direct_scheme="http", peer=None, forwarded_proto="https") == "http"


# --- effective client IP: the rightmost-untrusted-hop walk ------------------------


def test_client_ip_is_the_forwarded_client_behind_one_trusted_proxy() -> None:
    trusted = TrustedProxies.parse(["10.0.0.4"])
    assert trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="203.0.113.9") == "203.0.113.9"


def test_client_ip_walks_past_chained_trusted_proxies_to_the_first_untrusted_hop() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    # client, then two of our own chained proxies (both trusted) — the client wins.
    assert (
        trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="203.0.113.9, 10.1.1.1, 10.2.2.2")
        == "203.0.113.9"
    )


def test_client_ip_stops_at_the_first_untrusted_hop_from_the_right() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    # The middle hop is not one of ours — we only trust as far as our proxies reach, so
    # that untrusted hop (not the spoofable leftmost entry) is treated as the client.
    assert (
        trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="1.1.1.1, 198.51.100.7, 10.1.1.1")
        == "198.51.100.7"
    )


def test_client_ip_ignores_a_forged_header_from_an_untrusted_peer() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    # A direct client spoofing X-Forwarded-For cannot move its throttle bucket.
    assert trusted.effective_client_ip(direct_peer="203.0.113.9", forwarded_for="1.2.3.4") == "203.0.113.9"


def test_client_ip_falls_back_to_the_direct_peer_when_all_hops_are_trusted() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for="10.1.1.1, 10.2.2.2") == "10.0.0.4"


def test_client_ip_falls_back_to_the_direct_peer_on_an_absent_header() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_client_ip(direct_peer="10.0.0.4", forwarded_for=None) == "10.0.0.4"


def test_client_ip_tolerates_the_unknown_sentinel_peer() -> None:
    trusted = TrustedProxies.parse(["10.0.0.0/8"])
    assert trusted.effective_client_ip(direct_peer="unknown", forwarded_for="1.2.3.4") == "unknown"
