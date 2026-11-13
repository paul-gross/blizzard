"""GitHub's credential refusals: one test per classifier row, and the diagnostic each refusal carries (unit tier)."""

from __future__ import annotations

from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal, ForgeDiagnostic
from blizzard.hub.forge.internal.github_refusals import CallCredential, classify_auth_refusal

_ENDPOINT = "GET /repos/{owner}/{repo}/commits/{ref}/check-runs"


def _classify(
    status: int, message: str, headers: dict[str, str] | None = None, *, withheld: bool = False
) -> AuthRefusal:
    return classify_auth_refusal(
        status,
        headers or {},
        message,
        documentation_url=None,
        operation="checks",
        endpoint=_ENDPOINT,
        repository="acme/widget",
        withheld=withheld,
    ).refusal


def test_a_401_is_an_invalid_credential() -> None:
    assert _classify(401, "Bad credentials") is AuthRefusal.CREDENTIAL_INVALID


def test_a_403_requiring_sso_by_header_or_by_message_is_sso_required() -> None:
    assert _classify(403, "Forbidden", {"X-GitHub-SSO": "required; url=https://github.com/orgs/acme/sso"}) is (
        AuthRefusal.SSO_REQUIRED
    )
    assert _classify(403, "Resource protected by organization SAML enforcement.") is AuthRefusal.SSO_REQUIRED


def test_a_403_on_a_call_the_tokens_variant_withholds_is_an_unsupported_token() -> None:
    assert _classify(403, "Resource not accessible by personal access token", withheld=True) is (
        AuthRefusal.TOKEN_UNSUPPORTED
    )


def test_an_invalid_credential_and_sso_take_precedence_over_a_withheld_call() -> None:
    assert _classify(401, "Bad credentials", withheld=True) is AuthRefusal.CREDENTIAL_INVALID
    assert _classify(403, "Forbidden", {"X-GitHub-SSO": "required"}, withheld=True) is AuthRefusal.SSO_REQUIRED


def test_the_diagnostic_names_the_credential_the_call_carried() -> None:
    diagnostic = classify_auth_refusal(
        403,
        {},
        "Forbidden",
        documentation_url=None,
        operation="checks",
        endpoint=_ENDPOINT,
        repository="acme/widget",
        credential=CallCredential("deploy", "github.token", "fine-grained"),
    ).diagnostic

    assert (diagnostic.credential, diagnostic.binding, diagnostic.variant) == ("deploy", "github.token", "fine-grained")


def test_any_other_403_is_a_missing_permission() -> None:
    assert _classify(403, "Resource not accessible by personal access token") is AuthRefusal.PERMISSION_MISSING


def test_the_diagnostic_carries_githubs_answer_sanitized() -> None:
    classified = classify_auth_refusal(
        403,
        {"X-GitHub-Request-Id": "ABCD:1234", "X-Accepted-GitHub-Permissions": "checks=read"},
        "Resource not accessible by token ghp_secretvalue",
        documentation_url="https://docs.github.com/rest/checks",
        operation="checks",
        endpoint=_ENDPOINT,
        repository="acme/widget",
    )

    assert classified.diagnostic == ForgeDiagnostic(
        status=403,
        operation="checks",
        endpoint=_ENDPOINT,
        repository="acme/widget",
        message="Resource not accessible by token [redacted]",
        documentation_url="https://docs.github.com/rest/checks",
        request_id="ABCD:1234",
        accepted_permissions="checks=read",
    )
