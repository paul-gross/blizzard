"""Credential bindings: how a credential authenticates against a forge of one kind, what settings it takes, the
authority it acts as, and what configuration alone says it can do.

A binding is looked up by name in :data:`CREDENTIAL_BINDINGS` and passed to the code that needs it; no record holds
one. It imports no adapter: it sits beside :data:`~blizzard.hub.domain.config.forges.FORGE_KIND_CAPABILITIES`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.credential_setting_kind import SettingKind
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
        """Each setting the binding takes, by key, with the values it accepts — none for a setting not a choice."""
        ...

    @property
    def setting_kinds(self) -> Mapping[str, SettingKind]:
        """What each setting the binding takes, by key, accepts."""
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

    @property
    def setting_kinds(self) -> Mapping[str, SettingKind]:
        return {"variant": SettingKind.CHOICE}

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


#: The settings a ``github.app-installation`` credential takes, each a positive integer.
_APP_SETTINGS = ("app_id", "installation_id")


@domain_model
@dataclass(frozen=True)
class GitHubAppInstallationBinding:
    """A GitHub App's installation, acting through installation tokens the hub mints with the App's private key — the
    secret's value. ``app_id`` and ``installation_id`` are both required, each a positive integer; the authority is
    the installation, so every credential naming it shares one budget and one write lane."""

    name: str = "github.app-installation"
    forge_kind: str = "github"
    supplied: bool = False

    @property
    def setting_choices(self) -> Mapping[str, tuple[str, ...]]:
        return dict.fromkeys(_APP_SETTINGS, ())

    @property
    def setting_kinds(self) -> Mapping[str, SettingKind]:
        return dict.fromkeys(_APP_SETTINGS, SettingKind.POSITIVE_INTEGER)

    def validate(self, settings: Mapping[str, object]) -> dict[str, object]:
        for key in settings:
            if key not in _APP_SETTINGS:
                raise CredentialSettingsInvalid(
                    key, f"{self.name} takes no setting {key!r} (known: {list(_APP_SETTINGS)})"
                )
        canonical: dict[str, object] = {}
        for key in _APP_SETTINGS:
            if key not in settings:
                raise CredentialSettingsInvalid(key, f"{self.name} requires {key}")
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise CredentialSettingsInvalid(key, f"must be a positive integer, got {value!r}")
            canonical[key] = value
        return canonical

    def authority(self, secret_name: str, settings: Mapping[str, object]) -> str:
        return f"app/{settings['app_id']}/installation/{settings['installation_id']}"

    def declared_offers(self, settings: Mapping[str, object], kind: frozenset[Capability]) -> frozenset[Capability]:
        return kind


#: Every binding a credential may name, by name.
CREDENTIAL_BINDINGS: Mapping[str, CredentialBinding] = {
    binding.name: binding for binding in (GitHubTokenBinding(), GitHubAppInstallationBinding())
}


def bindings_for_kind(kind: str) -> tuple[str, ...]:
    """The name of every binding a credential on a forge of ``kind`` may name."""
    return tuple(binding.name for binding in CREDENTIAL_BINDINGS.values() if binding.forge_kind == kind)
