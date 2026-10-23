"""The defaults of the hub's config keys, named once for the loader and for the registry that documents them."""

from __future__ import annotations

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421

#: The human-auth rollout knob: `none` resolves every request to an implicit identity; `oauth` activates sessions.
AUTH_MODE_NONE = "none"
AUTH_MODE_OAUTH = "oauth"

#: Forge-status sweep cadence, in seconds.
DEFAULT_ANNOTATION_INTERVAL_SECONDS = 120
#: A hub action pass's bound, in seconds.
DEFAULT_ACTION_PASS_SECONDS = 300
DEFAULT_FOLLOW_LATEST = False
DEFAULT_CLOSE_FORGE_WRITES_ENABLED = True
