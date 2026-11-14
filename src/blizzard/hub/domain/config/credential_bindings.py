"""Credential bindings: how a credential authenticates against a forge of one kind, what settings it takes, the
authority it acts as, and what configuration alone says it can do.

A binding is looked up by name in :data:`CREDENTIAL_BINDINGS` and passed to the code that needs it; no record holds
one. It imports no adapter: it sits beside :data:`~blizzard.hub.domain.config.forges.FORGE_KIND_CAPABILITIES`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.errors import ConfigFieldError

#: What a fine-grained GitHub token cannot do, whatever its forge's kind offers: GitHub grants a fine-grained token no
#: read of check runs.
FINE_GRAINED_WITHHELD: frozenset[Capability] = frozenset({Capability.CHECKS_READ})


class CredentialSettingsInvalid(ConfigFieldError):
    """A credential's settings are refused by its binding, naming the setting."""

    code = "credential_settings_invalid"

    def __init__(self, setting: str, message: str) -> None:
        super().__init__(f"settings.{setting}", message)
        self.setting = setting


class CredentialBinding(Protocol):
    """How a credential authenticates: ``name`` is ``<forge kind>.<binding>``, and ``forge_kind`` the kind of forge
    it authenticates against. A ``supplied`` binding's secret is replaced by the operator; any other's access the hub
    renews."""

    @property
    def name(self) -> str: ...

    @property
    def forge_kind(self) -> str: ...

    @property
    def supplied(self) -> bool: ...

    @property
    def setting_choices(self) -> Mapping[str, tuple[str, ...]]:
        """Each setting the binding takes, by key, with the values it accepts."""
        ...

    def validate(self, settings: Mapping[str, object]) -> dict[str, object]:
        """The canonical form of ``settings``; :class:`CredentialSettingsInvalid` naming the setting it refuses."""
        ...

    def authority(self, secret_name: str, settings: Mapping[str, object]) -> str:
        """The identity a credential of these canonical settings, reading ``secret_name``, acts as."""
        ...

    def declared_offers(self, settings: Mapping[str, object], kind: frozenset[Capability]) -> frozenset[Capability]:
        """What configuration alone says the credential can do on a forge whose kind offers ``kind``; the runtime
        may narrow it further."""
        ...


#: The values a ``github.token`` credential's ``variant`` takes.
_TOKEN_VARIANTS = ("classic", "fine-grained")


@domain_model
@dataclass(frozen=True)
class GitHubTokenBinding:
    """A GitHub personal access token the operator supplies, as the secret's value. Its one setting, ``variant``, is
    ``classic`` or ``fine-grained``, or absent; a fine-grained token is withheld :data:`FINE_GRAINED_WITHHELD`."""

    name: str = "github.token"
    forge_kind: str = "github"
    supplied: bool = True

    @property
    def setting_choices(self) -> Mapping[str, tuple[str, ...]]:
        return {"variant": _TOKEN_VARIANTS}

    def validate(self, settings: Mapping[str, object]) -> dict[str, object]:
        for key in settings:
            if key != "variant":
                raise CredentialSettingsInvalid(key, f"{self.name} takes no setting {key!r} (known: ['variant'])")
        if "variant" not in settings:
            return {}
        variant = settings["variant"]
        if variant not in _TOKEN_VARIANTS:
            raise CredentialSettingsInvalid("variant", f"must be one of {list(_TOKEN_VARIANTS)}, got {variant!r}")
        return {"variant": variant}

    def authority(self, secret_name: str, settings: Mapping[str, object]) -> str:
        return f"secret/{secret_name}"

    def declared_offers(self, settings: Mapping[str, object], kind: frozenset[Capability]) -> frozenset[Capability]:
        if settings.get("variant") == "fine-grained":
            return kind - FINE_GRAINED_WITHHELD
        return kind


#: Every binding a credential may name, by name.
CREDENTIAL_BINDINGS: Mapping[str, CredentialBinding] = {binding.name: binding for binding in (GitHubTokenBinding(),)}
