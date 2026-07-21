"""The hub's IdP signing-key lifecycle (issue #95, decision D1) — ``SigningKeyService``.

The hub becomes the fleet's identity provider only under ``auth.mode = "oauth"``: this
service owns the RS256 keypair a runner's federation bounce is minted/verified against
(``hub/api/idp.py``), the JWKS a runner fetches to verify a ``kid``, and rotation. Under
``auth.mode = "none"`` no instance of this service is ever constructed (``hub/app.py``'s
``build_hosted_app``) — there is no keypair on disk, no JWKS, no IdP surface, mirroring
#92's own "no login mechanism under none".

**Storage.** The private key material lives in the hub's own data dir
(``config.data_dir / "auth" / "signing-keys"``), never in the store, never in config,
never in the DB — owner-only permissions throughout (``0700`` dir, ``0600`` files),
mirroring ``runner/listeners.py``'s own "filesystem permissions are the access control"
posture for its unix socket. A small ``meta.json`` names which ``kid`` is ``current``
and which (if any) is ``previous``; each keypair's private key is its own
``<kid>.pem`` file. Generated lazily on first use (a fresh deployment mints its first
keypair the first time a signing service is constructed under ``oauth`` mode) —
idempotent across restarts, since ``meta.json`` already naming a ``current`` kid is
read back rather than regenerated.

**Rotation** (``rotate()``, driven by ``blizzard hub rotate-signing-key``) mints a fresh
keypair, demotes the old ``current`` to ``previous`` (dropping whatever ``previous`` key
existed before — the JWKS publishes at most two generations), and persists the new
``meta.json`` before returning. A runner picks up the new key with **no restart**: its
own JWKS cache (``runner/auth/jwks.py``) re-fetches on an unknown ``kid``, and this
service's own in-memory state is mutated in place by the very call the CLI verb makes
against the live hub process — there is no separate reload step.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

#: RS256 key size (decision D1) — the conventional minimum for an RSA signing key.
_KEY_SIZE_BITS = 2048
_META_FILENAME = "meta.json"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_KID_BYTES = 12


@dataclass(frozen=True)
class _KeyMeta:
    current_kid: str
    previous_kid: str | None


class SigningKeyService:
    """Load/generate the hub's IdP signing keypair, sign claims, and serve JWKS.

    One instance lives for a hub process's whole run (``hub/composition.py``'s
    ``build_services``, under ``oauth`` mode only) — ``rotate()`` mutates its in-memory
    state and persists it, so every subsequent ``sign``/``jwks`` call in this same
    process sees the rotation immediately.
    """

    def __init__(self, keys_dir: Path) -> None:
        self._dir = keys_dir
        self._meta = self._load_or_generate()

    # --- public surface -------------------------------------------------

    def sign(self, claims: dict[str, object], *, now: datetime, ttl: timedelta) -> str:
        """Sign ``claims`` with the current key, stamping ``iat``/``exp`` from ``now``
        (``bzh:injected-clock`` — the caller threads the hub's own clock through, never
        ``datetime.now()`` here)."""
        payload = {**claims, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
        private_pem = self._read_private_pem(self._meta.current_kid)
        return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": self._meta.current_kid})

    def public_jwks(self) -> dict[str, object]:
        """Current + previous public keys, each ``kid``-tagged (issue #95)."""
        kids = [self._meta.current_kid]
        if self._meta.previous_kid is not None:
            kids.append(self._meta.previous_kid)
        return {"keys": [self._public_jwk(kid) for kid in kids]}

    def rotate(self) -> None:
        """Mint a fresh current key, demoting the old current to previous (dropping
        whatever previous key existed before it)."""
        old_current = self._meta.current_kid
        new_kid = self._mint_keypair()
        old_previous = self._meta.previous_kid
        self._meta = _KeyMeta(current_kid=new_kid, previous_kid=old_current)
        self._write_meta(self._meta)
        if old_previous is not None:
            self._key_path(old_previous).unlink(missing_ok=True)

    # --- key material -----------------------------------------------------

    def _load_or_generate(self) -> _KeyMeta:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.chmod(_DIR_MODE)
        meta_path = self._dir / _META_FILENAME
        if meta_path.exists():
            raw = json.loads(meta_path.read_text())
            return _KeyMeta(current_kid=raw["current_kid"], previous_kid=raw.get("previous_kid"))
        kid = self._mint_keypair()
        meta = _KeyMeta(current_kid=kid, previous_kid=None)
        self._write_meta(meta)
        return meta

    def _mint_keypair(self) -> str:
        kid = secrets.token_hex(_KID_BYTES)
        key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE_BITS)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path = self._key_path(kid)
        path.write_bytes(pem)
        path.chmod(_FILE_MODE)
        return kid

    def _read_private_pem(self, kid: str) -> bytes:
        return self._key_path(kid).read_bytes()

    def _public_jwk(self, kid: str) -> dict[str, object]:
        private_pem = self._read_private_pem(kid)
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        public_key = private_key.public_key()  # type: ignore[union-attr]
        jwk = json.loads(RSAAlgorithm.to_jwk(public_key))  # type: ignore[arg-type]
        jwk["kid"] = kid
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        return jwk

    def _key_path(self, kid: str) -> Path:
        return self._dir / f"{kid}.pem"

    def _write_meta(self, meta: _KeyMeta) -> None:
        path = self._dir / _META_FILENAME
        path.write_text(json.dumps({"current_kid": meta.current_kid, "previous_kid": meta.previous_kid}))
        path.chmod(_FILE_MODE)
