"""The ``github`` conformer — plain OAuth2 code flow, ``GET /user`` + ``GET /user/emails`` for the
primary email and every verified address on the account (package-private).

All ``httpx2`` usage is confined here (``bzh:dependency-inversion``). ``web_base``/``api_base`` default
to real GitHub's split hosts but are overridable to point at a stub IdP."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx2

from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.auth.oauth.provider import OAuthExchangeError

_log = get_logger("blizzard.hub.auth.oauth")

_DEFAULT_WEB_BASE = "https://github.com"
_DEFAULT_API_BASE = "https://api.github.com"


class GithubProvider:
    """A GitHub-style OAuth2 provider — code flow, numeric ``id`` as subject, the primary
    email and every verified address via the ``user:email`` scope.

    GitHub reports an address as verified only once its owner has proven it, so its
    verified addresses are authoritative."""

    type = "github"
    emails_authoritative = True

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        http_client: httpx2.Client,
        web_base: str = _DEFAULT_WEB_BASE,
        api_base: str = _DEFAULT_API_BASE,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._web_base = web_base.rstrip("/")
        self._api_base = api_base.rstrip("/")

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": "user:email",
            "state": state,
        }
        return f"{self._web_base}/login/oauth/authorize?{urlencode(params)}"

    def exchange(self, *, code: str, redirect_uri: str) -> ProviderIdentity:
        try:
            token_resp = self._http.post(
                f"{self._web_base}/login/oauth/access_token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]
            headers = {"Authorization": f"token {access_token}", "Accept": "application/vnd.github+json"}
            user_resp = self._http.get(f"{self._api_base}/user", headers=headers)
            user_resp.raise_for_status()
            user = user_resp.json()
            emails = self._emails(headers)
        except (httpx2.HTTPError, KeyError) as exc:
            _log.warning("github exchange failed", provider=self.name, detail=str(exc))
            raise OAuthExchangeError(f"github exchange failed for provider {self.name!r}") from exc
        subject = user.get("id")
        if subject is None:
            raise OAuthExchangeError(f"github /user response for provider {self.name!r} carries no 'id'")
        handle = user.get("login") or str(subject)
        primary = next((entry for entry in emails if entry.get("primary")), {})
        primary_email = primary.get("email")
        return ProviderIdentity(
            subject=str(subject),
            handle=str(handle),
            email=str(primary_email) if primary_email is not None else None,
            email_verified=bool(primary.get("verified", False)),
            verified_emails=tuple(
                str(entry["email"]) for entry in emails if entry.get("verified") is True and entry.get("email")
            ),
            emails_authoritative=self.emails_authoritative,
        )

    def _emails(self, headers: dict[str, str]) -> list[dict[str, object]]:
        """Every entry of ``/user/emails`` — the primary and each secondary address alike."""
        resp = self._http.get(f"{self._api_base}/user/emails", headers=headers)
        resp.raise_for_status()
        return resp.json()
