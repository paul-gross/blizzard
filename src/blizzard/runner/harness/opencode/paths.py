"""Where OpenCode keeps its credential document and user config — one resolver each, fed an env mapping."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def resolve_opencode_auth_path(env: Mapping[str, str]) -> Path | None:
    """OpenCode's ``auth.json`` as the process owning ``env`` would read it: ``XDG_DATA_HOME``
    when non-empty, otherwise ``HOME/.local/share``. ``None`` with neither set — an unrooted
    relative path would read whatever sits under the cwd, so this reads as no credential."""
    data_home = env.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "opencode" / "auth.json"
    home = env.get("HOME")
    if not home:
        return None
    return Path(home) / ".local" / "share" / "opencode" / "auth.json"


def resolve_opencode_config_dir(env: Mapping[str, str]) -> Path | None:
    """OpenCode's user config directory as the process owning ``env`` would read it: ``XDG_CONFIG_HOME``
    when non-empty, otherwise ``HOME/.config``, then ``opencode``. ``None`` with neither set — the daemon's own
    home is not the worker's, so this reads as no user config."""
    config_home = env.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "opencode"
    home = env.get("HOME")
    if not home:
        return None
    return Path(home) / ".config" / "opencode"
