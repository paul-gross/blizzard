"""OAuth provider conformers — package-private (issue #92, ``bzh:dependency-inversion``).

Everything under this package is confined to ``hub/auth/oauth/`` and must not be
imported from outside it; a consumer depends on :class:`~blizzard.hub.auth.oauth.
provider.IOAuthProvider` (the feature-package root) instead. All ``httpx``/JWT/
provider-wire-shape knowledge is confined here.
"""

from __future__ import annotations
