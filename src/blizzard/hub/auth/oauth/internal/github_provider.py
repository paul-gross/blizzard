"""The ``github`` conformer — plain OAuth2 code flow, ``GET /user`` +
``GET /user/emails`` for the verified primary email (issue #92, package-private).

All ``httpx`` usage is confined here (``bzh:dependency-inversion``). ``web_base``/
``api_base`` default to real GitHub's split hosts but are overridable — the stub IdP
(``blizzard-mock``) serves both shapes at one origin, so a service-tier scenario points
both at it via one configured ``[[auth.oauth.provider]] api_base`` (mirroring
``PmSourceConfig.api_base``'s own GHE-override precedent).
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.auth.oauth.provider import OAuthExchangeError

_log = get_logger("blizzard.hub.auth.oauth")

_DEFAULT_WEB_BASE = "https://github.com"
_DEFAULT_API_BASE = "https://api.github.com"


class GithubProvider:
    """A GitHub-style OAuth2 provider — code flow, numeric ``id`` as subject, the
    verified primary email via the ``user:email`` scope."""

    type = "github"

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.Client,
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
            email, email_verified = self._primary_verified_email(headers)
        except (httpx.HTTPError, KeyError) as exc:
            _log.warning("github exchange failed", provider=self.name, detail=str(exc))
            raise OAuthExchangeError(f"github exchange failed for provider {self.name!r}") from exc
        subject = user.get("id")
        if subject is None:
            raise OAuthExchangeError(f"github /user response for provider {self.name!r} carries no 'id'")
        handle = user.get("login") or str(subject)
        return ProviderIdentity(subject=str(subject), handle=str(handle), email=email, email_verified=email_verified)

    def _primary_verified_email(self, headers: dict[str, str]) -> tuple[str | None, bool]:
        resp = self._http.get(f"{self._api_base}/user/emails", headers=headers)
        resp.raise_for_status()
        for entry in resp.json():
            if entry.get("primary"):
                return entry.get("email"), bool(entry.get("verified", False))
        return None, False
