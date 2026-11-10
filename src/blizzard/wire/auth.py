"""Authentication refusals shared by runner-reached hub operations."""

from typing import Final, Literal

from pydantic import Field

from blizzard.wire.problem import ProblemDetails


class AuthenticationRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:authentication-required"]]):
    """A required credential or session is absent."""


class AuthenticationRevokedProblem(ProblemDetails[Literal["urn:blizzard:problem:authentication-revoked"]]):
    """The presented bearer credential has been revoked."""


class AuthenticationUnresolvedProblem(ProblemDetails[Literal["urn:blizzard:problem:authentication-unresolved"]]):
    """The presented bearer credential does not resolve to the required principal."""


class PermissionRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:permission-required"]]):
    """The authenticated caller lacks a required permission."""


class MachineCredentialForbiddenProblem(ProblemDetails[Literal["urn:blizzard:problem:machine-credential-forbidden"]]):
    """A machine credential is not valid for this operation."""


class RunnerCredentialForbiddenProblem(ProblemDetails[Literal["urn:blizzard:problem:runner-credential-forbidden"]]):
    """A resolved runner credential was presented on an operator verb."""


class IdpDisabledProblem(ProblemDetails[Literal["urn:blizzard:problem:idp-disabled"]]):
    """The hub's IdP surface or its signing key is unavailable."""


class FederationResponseModeProblem(ProblemDetails[Literal["urn:blizzard:problem:federation-response-mode"]]):
    """The requested authorization response mode is not supported."""


class FederationClientRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:federation-client-refused"]]):
    """Unknown client, unregistered redirect, or missing membership, deliberately indistinguishable."""


class FederationNoProviderProblem(ProblemDetails[Literal["urn:blizzard:problem:federation-no-provider"]]):
    """No existing hub session and no configured authentication provider."""


class CliRedirectRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:cli-redirect-refused"]]):
    """The CLI redirect is not a registered loopback or out-of-band URI."""


class CliPkceRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:cli-pkce-required"]]):
    """CLI authorization lacks a valid PKCE challenge."""


class CliTokenRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:cli-token-refused"]]):
    """Code, verifier, or redirect is invalid, deliberately without identifying which."""


class LoginDisabledProblem(ProblemDetails[Literal["urn:blizzard:problem:login-disabled"]]):
    """Provider login is disabled."""


class LoginProviderUnknownProblem(ProblemDetails[Literal["urn:blizzard:problem:login-provider-unknown"]]):
    """The requested provider is not configured."""


class LoginThrottledProblem(ProblemDetails[Literal["urn:blizzard:problem:login-throttled"]]):
    """The login attempt was rate limited."""


class LoginFailedProblem(ProblemDetails[Literal["urn:blizzard:problem:login-failed"]]):
    """A provider callback failed without exposing which check failed."""

    error: Literal["login_failed"] = Field(deprecated=True)


class SsoRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:sso-refused"]]):
    """A provider callback's state belongs to a different provider."""

    error: Literal["sso_refused"] = Field(deprecated=True)


AUTHENTICATION_REQUIRED: Final = "urn:blizzard:problem:authentication-required"


AUTHENTICATION_REVOKED: Final = "urn:blizzard:problem:authentication-revoked"


AUTHENTICATION_UNRESOLVED: Final = "urn:blizzard:problem:authentication-unresolved"


PERMISSION_REQUIRED: Final = "urn:blizzard:problem:permission-required"


MACHINE_CREDENTIAL_FORBIDDEN: Final = "urn:blizzard:problem:machine-credential-forbidden"


RUNNER_CREDENTIAL_FORBIDDEN: Final = "urn:blizzard:problem:runner-credential-forbidden"


IDP_DISABLED: Final = "urn:blizzard:problem:idp-disabled"


FEDERATION_RESPONSE_MODE: Final = "urn:blizzard:problem:federation-response-mode"


FEDERATION_CLIENT_REFUSED: Final = "urn:blizzard:problem:federation-client-refused"


FEDERATION_NO_PROVIDER: Final = "urn:blizzard:problem:federation-no-provider"


CLI_REDIRECT_REFUSED: Final = "urn:blizzard:problem:cli-redirect-refused"


CLI_PKCE_REQUIRED: Final = "urn:blizzard:problem:cli-pkce-required"


CLI_TOKEN_REFUSED: Final = "urn:blizzard:problem:cli-token-refused"


LOGIN_DISABLED: Final = "urn:blizzard:problem:login-disabled"


LOGIN_PROVIDER_UNKNOWN: Final = "urn:blizzard:problem:login-provider-unknown"


LOGIN_THROTTLED: Final = "urn:blizzard:problem:login-throttled"


LOGIN_FAILED: Final = "urn:blizzard:problem:login-failed"


SSO_REFUSED: Final = "urn:blizzard:problem:sso-refused"


AUTHENTICATION_TYPES: frozenset[str] = frozenset(
    {AUTHENTICATION_REQUIRED, AUTHENTICATION_REVOKED, AUTHENTICATION_UNRESOLVED}
)


AUTH_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        AUTHENTICATION_REQUIRED,
        AUTHENTICATION_REVOKED,
        AUTHENTICATION_UNRESOLVED,
        PERMISSION_REQUIRED,
        MACHINE_CREDENTIAL_FORBIDDEN,
        RUNNER_CREDENTIAL_FORBIDDEN,
        IDP_DISABLED,
        FEDERATION_RESPONSE_MODE,
        FEDERATION_CLIENT_REFUSED,
        FEDERATION_NO_PROVIDER,
        CLI_REDIRECT_REFUSED,
        CLI_PKCE_REQUIRED,
        CLI_TOKEN_REFUSED,
        LOGIN_DISABLED,
        LOGIN_PROVIDER_UNKNOWN,
        LOGIN_THROTTLED,
        LOGIN_FAILED,
        SSO_REFUSED,
    }
)
