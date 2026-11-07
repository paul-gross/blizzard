"""Reviewed candidates under src/blizzard/runner/auth/internal, not a whole-runner census."""

REVIEWED = {
    ("http_hub_auth_mode.py", "HttpHubAuthMode.enabled", "self._http.get"): "httpx client probes the hub JWKS endpoint",
    ("http_jwks_cache.py", "JwksCache._refresh", "self._http.get"): "httpx client fetches the configured JWKS URL",
    ("http_jwks_cache.py", "JwksCache.key_for", "self._keys.get"): "local dictionary of public keys indexed by kid",
}
