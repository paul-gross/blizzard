"""How a client finds the operator's managed OpenCode service: the registration file it writes on listen."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import decode_json


class RegistrationError(ValueError):
    """The registration file exists but does not hold a usable registration."""


def registration_path(env: Mapping[str, str]) -> Path | None:
    """The default channel's registration as the process owning ``env`` reads it: ``XDG_STATE_HOME`` when non-empty,
    otherwise ``HOME/.local/state``, then ``opencode/service.json``. ``None`` with neither set."""
    state_home = env.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "opencode" / "service.json"
    home = env.get("HOME")
    if not home:
        return None
    return Path(home) / ".local" / "state" / "opencode" / "service.json"


@domain_model
@dataclass(frozen=True)
class ServiceRegistration:
    """One running service's registration. ``id`` is new on every start, so it names one service lifetime; the
    password never appears in a repr."""

    id: str
    version: str
    url: str
    pid: int
    password: str = field(repr=False)

    @classmethod
    def parse(cls, raw: str | bytes) -> ServiceRegistration:
        try:
            decoded = decode_json(raw)
        except ValueError as exc:
            raise RegistrationError("the OpenCode service registration is not JSON") from exc
        if not isinstance(decoded, dict):
            raise RegistrationError("the OpenCode service registration is not an object")
        texts = {name: decoded.get(name) for name in ("id", "version", "url", "password")}
        raw_pid = decoded.get("pid")
        pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
        missing = [name for name, value in texts.items() if not isinstance(value, str) or not value]
        if pid is None:
            missing.append("pid")
        if missing or pid is None:
            raise RegistrationError(f"the OpenCode service registration lacks {', '.join(missing)}")
        return cls(
            id=str(texts["id"]),
            version=str(texts["version"]),
            url=str(texts["url"]).rstrip("/"),
            pid=pid,
            password=str(texts["password"]),
        )


def read_registration(path: Path) -> ServiceRegistration | None:
    """The registration at ``path``, or ``None`` when no service is registered there."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    return ServiceRegistration.parse(raw)


__all__ = ["RegistrationError", "ServiceRegistration", "read_registration", "registration_path"]
