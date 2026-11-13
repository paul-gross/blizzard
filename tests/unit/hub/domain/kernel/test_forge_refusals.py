"""The forge-refusal vocabulary: the sanitizer keeps no secret and no more than its bound, each refusal has its sentence
and remedy, and the event detail carries the diagnostic whole (unit tier)."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from blizzard.hub.domain.kernel.forge_refusals import (
    REDACTED,
    SANITIZED_TEXT_MAX,
    AuthRefusal,
    ClassifiedRefusal,
    ForgeDiagnostic,
    forge_auth_refused_detail,
    refusal_remedy,
    refusal_sentence,
    sanitize_forge_text,
)

_WORD = st.text(alphabet="abcXYZ019_-.", min_size=1, max_size=40)


def _github_token(parts: tuple[str, str]) -> str:
    return f"gh{parts[0]}_{parts[1]}"


def _jwt(parts: tuple[str, str, str]) -> str:
    return f"eyJ{parts[0]}.{parts[1]}.{parts[2]}"


def _pem(parts: tuple[str, str, bool]) -> str:
    label, body, ended = parts
    return f"-----BEGIN {label}-----\n{body}\n" + (f"-----END {label}-----" if ended else "")


def _authorization(parts: tuple[str, str, str, str]) -> str:
    return "".join(parts)


_SECRETS = st.one_of(
    st.tuples(st.sampled_from("pousr"), _WORD).map(_github_token),
    _WORD.map("github_pat_".__add__),
    st.tuples(_WORD, _WORD, _WORD).map(_jwt),
    st.tuples(st.sampled_from(["RSA PRIVATE KEY", "PRIVATE KEY", "CERTIFICATE"]), _WORD, st.booleans()).map(_pem),
    st.tuples(
        st.sampled_from(["Authorization", "authorization", "AUTHORIZATION", '"Authorization"']),
        st.sampled_from([": ", "=", ":", " = ", '": "']),
        st.sampled_from(["", "token ", "Bearer ", "basic "]),
        _WORD,
    ).map(_authorization),
)
_FILLER = st.text(max_size=80)
_TEXT = st.lists(st.one_of(_SECRETS, _FILLER), max_size=12).map("".join)

_FOUND_SECRET = (
    re.compile(r"gh[pousr]_\w+"),
    re.compile(r"github_pat_\w+"),
    re.compile(r"eyJ[\w-]*\.[\w-]*\.[\w-]*"),
    re.compile(r"-----BEGIN"),
)
_AUTHORIZATION = re.compile(r"authorization[\"']?\s*[:=]\s*[\"']?\s*", re.IGNORECASE)


def _leftover_authorization_value(text: str) -> str | None:
    for match in _AUTHORIZATION.finditer(text):
        rest = text[match.end() :]
        if rest and not REDACTED.startswith(rest[: len(REDACTED)]) and not rest.startswith("…"):
            return rest[:40]
    return None


# No example database: a test reads no undeclared repo file (tests/support/repo_files.py).
@settings(database=None, max_examples=300)
@given(_TEXT)
def test_sanitized_text_carries_no_secret_and_stays_within_its_bound(text: str) -> None:
    sanitized = sanitize_forge_text(text)

    assert len(sanitized) <= SANITIZED_TEXT_MAX
    assert [pattern.pattern for pattern in _FOUND_SECRET if pattern.search(sanitized)] == []
    assert _leftover_authorization_value(sanitized) is None


def test_a_secret_glued_from_what_surrounded_another_is_redacted_too() -> None:
    assert sanitize_forge_text("ghghp_inner p_outer") == f"gh{REDACTED} p_outer"
    assert "ghp_" not in sanitize_forge_text("gh" + "ghp_inner" + "p_outer")


def test_plain_text_keeps_its_words_with_its_whitespace_collapsed() -> None:
    assert sanitize_forge_text("  Bad \n credentials\t ") == "Bad credentials"


def test_long_text_is_cut_to_the_bound() -> None:
    assert len(sanitize_forge_text("x" * 2000)) == SANITIZED_TEXT_MAX


def _diagnostic(accepted_permissions: str | None = None) -> ForgeDiagnostic:
    return ForgeDiagnostic(
        status=403,
        operation="checks",
        endpoint="GET /repos/{owner}/{repo}/commits/{ref}/check-runs",
        repository="acme/widget",
        message="Resource not accessible by personal access token",
        documentation_url=None,
        request_id="ABCD:1234",
        accepted_permissions=accepted_permissions,
    )


def test_every_refusal_has_a_sentence_and_a_remedy() -> None:
    for refusal in AuthRefusal:
        assert refusal_sentence(refusal)
        assert refusal_remedy(refusal, _diagnostic()).endswith(".")


def test_a_missing_permission_remedy_names_the_permissions_github_accepts() -> None:
    remedy = refusal_remedy(AuthRefusal.PERMISSION_MISSING, _diagnostic("checks=read"))

    assert remedy == "Grant checks=read to the token or the App installation."


def test_the_event_detail_carries_the_refusal_and_the_whole_diagnostic() -> None:
    detail = forge_auth_refused_detail(
        ClassifiedRefusal(AuthRefusal.PERMISSION_MISSING, _diagnostic()),
        forge_id="frg_1",
        chunk_id="ch_1",
        repository_id="repo_1",
    )

    assert detail == {
        "chunk_id": "ch_1",
        "repository_id": "repo_1",
        "work_source_id": None,
        "forge_id": "frg_1",
        "refusal": "permission-missing",
        "diagnostic": {
            "status": 403,
            "operation": "checks",
            "endpoint": "GET /repos/{owner}/{repo}/commits/{ref}/check-runs",
            "repository": "acme/widget",
            "message": "Resource not accessible by personal access token",
            "documentation_url": None,
            "request_id": "ABCD:1234",
            "accepted_permissions": None,
            "credential": None,
            "binding": None,
            "variant": None,
        },
    }


def test_an_unsupported_token_remedy_names_the_capability_the_call_needed() -> None:
    remedy = refusal_remedy(AuthRefusal.TOKEN_UNSUPPORTED, _diagnostic(), capability="checks.read")

    assert remedy == "Use a classic token or a GitHub App, or a landing policy that does not need `checks.read`."
