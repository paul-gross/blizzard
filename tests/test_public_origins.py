"""The declared browser-reachable origin set (issue #287): validation, derivation, selection."""

from __future__ import annotations

import pytest

from blizzard.foundation.public_origins import PublicOrigins

pytestmark = pytest.mark.unit

_CALLBACK = "/api/auth/callback"


class Invalid(RuntimeError):
    pass


def test_the_canonical_url_leads_and_extras_follow_in_declaration_order() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431", "http://localhost:8431", "https://tailnet.example:8431")
    assert origins.urls == ("http://127.0.0.1:8431", "http://localhost:8431", "https://tailnet.example:8431")
    assert origins.canonical == "http://127.0.0.1:8431"


def test_an_empty_canonical_url_drops_out_rather_than_declaring_a_blank_origin() -> None:
    origins = PublicOrigins.of("", "https://tailnet.example:8431")
    assert origins.urls == ("https://tailnet.example:8431",)
    assert origins.canonical == "https://tailnet.example:8431"


def test_declaring_nothing_yields_no_origins_and_no_canonical() -> None:
    origins = PublicOrigins.of("")
    assert origins.urls == ()
    assert origins.canonical is None
    assert origins.callback_uris(_CALLBACK) == ()


def test_a_trailing_slash_is_stripped_so_one_origin_cannot_register_as_two() -> None:
    origins = PublicOrigins.of("https://runner-a.example/")
    assert origins.callback_uris(_CALLBACK) == ("https://runner-a.example/api/auth/callback",)


def test_an_extra_repeating_the_canonical_authority_is_discarded_not_duplicated() -> None:
    origins = PublicOrigins.of("https://runner-a.example", "https://runner-a.example")
    assert origins.urls == ("https://runner-a.example",)


def test_one_callback_uri_is_derived_per_declared_origin() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431", "http://localhost:8431", "https://tailnet.example:8431")
    assert origins.callback_uris(_CALLBACK) == (
        "http://127.0.0.1:8431/api/auth/callback",
        "http://localhost:8431/api/auth/callback",
        "https://tailnet.example:8431/api/auth/callback",
    )


def test_select_returns_the_declared_origin_matching_the_hosts_authority() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431", "https://tailnet.example:8431")
    assert origins.select("tailnet.example:8431") == "https://tailnet.example:8431"
    assert origins.select("127.0.0.1:8431") == "http://127.0.0.1:8431"


def test_select_takes_the_scheme_from_the_declaration_so_a_proxy_cannot_downgrade_it() -> None:
    origins = PublicOrigins.of("https://tailnet.example:8431")
    assert origins.select("tailnet.example:8431") == "https://tailnet.example:8431"


def test_select_is_case_insensitive_on_the_host_and_tolerates_surrounding_space() -> None:
    origins = PublicOrigins.of("https://Tailnet.Example:8431")
    assert origins.select(" tailnet.example:8431 ") == "https://Tailnet.Example:8431"


def test_localhost_and_loopback_are_distinct_origins() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431")
    assert origins.select("127.0.0.1:8431") == "http://127.0.0.1:8431"
    assert origins.select("localhost:8431") is None


def test_select_is_port_sensitive() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431")
    assert origins.select("127.0.0.1:9999") is None


def test_an_undeclared_host_selects_nothing_rather_than_becoming_an_origin() -> None:
    origins = PublicOrigins.of("http://127.0.0.1:8431", "https://tailnet.example:8431")
    assert origins.select("evil.example") is None
    assert origins.select("") is None
    assert origins.select(None) is None


def test_well_formed_entries_are_carried_verbatim_so_they_round_trip_to_toml() -> None:
    raw = ["http://localhost:8431", "https://tailnet.example:8431"]
    assert PublicOrigins.entries(raw, Invalid) == ("http://localhost:8431", "https://tailnet.example:8431")


def test_a_single_origin_may_be_authored_as_a_bare_string() -> None:
    assert PublicOrigins.entries("http://localhost:8431", Invalid) == ("http://localhost:8431",)


def test_an_absent_declaration_reads_as_empty() -> None:
    assert PublicOrigins.entries(None, Invalid) == ()


@pytest.mark.parametrize("raw", [42, 3.5, True, {"url": "http://localhost:8431"}])
def test_a_wrongly_typed_declaration_fails_at_load_rather_than_disabling_federation(raw: object) -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries(raw, Invalid)


def test_an_empty_declaration_is_absent_rather_than_malformed() -> None:
    assert PublicOrigins.entries("", Invalid) == ()
    assert PublicOrigins.entries([""], Invalid) == ()
    assert PublicOrigins.entries(["", "http://localhost:8431"], Invalid) == ("http://localhost:8431",)


def test_entries_are_stripped_of_surrounding_whitespace() -> None:
    assert PublicOrigins.entries(["  http://localhost:8431  "], Invalid) == ("http://localhost:8431",)


@pytest.mark.parametrize(
    "entry",
    [
        "localhost:8431",
        "//localhost:8431",
        "ftp://localhost:8431",
        "https://",
    ],
)
def test_an_entry_that_is_not_an_http_origin_fails_at_load(entry: str) -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries([entry], Invalid)


@pytest.mark.parametrize(
    "entry", ["https://tailnet.example:8431/panel", "https://t.example?a=b", "https://t.example#f"]
)
def test_an_entry_carrying_a_path_query_or_fragment_fails_at_load(entry: str) -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries([entry], Invalid)


def test_a_bare_trailing_slash_is_normalized_at_admission_not_carried_verbatim() -> None:
    assert PublicOrigins.entries(["https://tailnet.example:8431/"], Invalid) == ("https://tailnet.example:8431",)


def test_declaring_one_authority_twice_fails_at_load() -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries(["https://tailnet.example:8431", "https://Tailnet.Example:8431"], Invalid)


def test_a_default_port_matches_a_host_that_omits_it() -> None:
    origins = PublicOrigins.of("https://runner.example:443", "http://plain.example:80")
    assert origins.select("runner.example") == "https://runner.example:443"
    assert origins.select("plain.example") == "http://plain.example:80"


def test_an_omitted_default_port_matches_a_host_that_names_it() -> None:
    origins = PublicOrigins.of("https://runner.example")
    assert origins.select("runner.example:443") == "https://runner.example"


def test_a_non_default_port_is_still_required_to_match() -> None:
    origins = PublicOrigins.of("https://runner.example:8431")
    assert origins.select("runner.example") is None


@pytest.mark.parametrize(
    "pair",
    [
        ("https://h.example:443", "https://h.example"),
        ("http://h.example:80", "http://h.example"),
        ("http://h.example", "https://h.example"),
    ],
)
def test_two_entries_a_browser_cannot_distinguish_fail_at_load(pair: tuple[str, str]) -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries(list(pair), Invalid)


def test_an_entry_carrying_userinfo_fails_at_load() -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries(["http://user:pw@h.example:8431"], Invalid)


def test_an_entry_with_an_unparseable_port_fails_at_load() -> None:
    with pytest.raises(Invalid):
        PublicOrigins.entries(["http://h.example:notaport"], Invalid)


def test_a_malformed_arriving_host_selects_nothing_rather_than_raising() -> None:
    origins = PublicOrigins.of("https://runner.example:8431")
    assert origins.select("runner.example:notaport") is None
