"""The legacy keys a hub still carries, as values — read and parsed by the config edge, judged by carry-over."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import domain_model

#: The forge variables a file-configured hub sets; each one set is a legacy key.
ENV_FORGE_URL = "BZ_FORGE_URL"
ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
ENV_FORGE_BASE_BRANCH = "BZ_FORGE_BASE_BRANCH"
ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
LEGACY_FORGE_VARIABLES = (ENV_FORGE_URL, ENV_FORGE_OWNER, ENV_FORGE_BASE_BRANCH, ENV_FORGE_TOKEN)


@domain_model
@dataclass(frozen=True)
class WorkSourceConfig:
    """One legacy ``[[work_source]]`` block, parsed only for :class:`LegacyKeys`.
    ``token_env`` names the environment variable carrying the credential, never the
    secret itself; ``api_base``/``web_base`` override the provider's default origins,
    and ``web_base`` derives from ``api_base`` when omitted."""

    name: str
    provider: str
    repo: str
    token_env: str
    #: Opt into the forge-status label sweep — canonical instance only; two writers fight.
    annotate: bool = False
    api_base: str | None = None
    web_base: str | None = None


@domain_model
@dataclass(frozen=True)
class LegacyKeys:
    """The legacy keys a hub still carries: its file's ``[[work_source]]`` blocks and the
    names of the legacy variables set in its environment — the forge variables and every
    variable a block's ``token_env`` names. Read from the parsed file, so a commented-out
    block never counts. Holds names only, never a value."""

    config_path: Path
    sources: tuple[WorkSourceConfig, ...]
    #: The legacy variables set in the environment, in declaration order.
    variables: tuple[str, ...]

    def present(self) -> bool:
        return bool(self.sources or self.variables)

    def locations(self) -> tuple[str, ...]:
        """Where each key was found — the file and block, or the variable — never a value."""
        return (
            *(f'{self.config_path.name} [[work_source]] "{source.name}"' for source in self.sources),
            *(f"environment {name}" for name in self.variables),
        )
