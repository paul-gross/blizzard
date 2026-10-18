from __future__ import annotations

import pytest

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    INTEGER,
    OPEN_TABLE,
    STRICT_CONFIG_ENVIRONMENT_VARIABLE,
    STRING,
    TABLE,
    ConfigKey,
    ConfigRegistry,
    ConfigView,
    UndeclaredConfigKey,
)

pytestmark = pytest.mark.unit

_REGISTRY = ConfigRegistry(
    "demo",
    (
        ConfigKey("port", INTEGER, default=80),
        ConfigKey("flags", OPEN_TABLE),
        ConfigKey("auth", TABLE),
        ConfigKey("auth.superuser", STRING),
        ConfigKey("runner", ARRAY_OF_TABLES),
        ConfigKey("runner[].name", STRING, required=True),
    ),
)
_DOCUMENT = {
    "port": 81,
    "stray": 1,
    "flags": {"probe": True},
    "auth": {"superuser": "root", "stray": 1},
    "runner": [{"name": "a", "stray": 1}],
}


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STRICT_CONFIG_ENVIRONMENT_VARIABLE, "1")


@pytest.fixture
def lenient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STRICT_CONFIG_ENVIRONMENT_VARIABLE, raising=False)


def _root() -> ConfigView:
    return ConfigView.root(_REGISTRY, _DOCUMENT)


@pytest.mark.usefixtures("strict")
def test_an_undeclared_read_names_the_path_and_the_registry() -> None:
    with pytest.raises(UndeclaredConfigKey, match=r"'stray'.*demo") as raised:
        _root().get("stray")
    assert (raised.value.path, raised.value.registry) == ("stray", "demo")


@pytest.mark.usefixtures("strict")
def test_a_presence_test_of_an_undeclared_key_raises() -> None:
    with pytest.raises(UndeclaredConfigKey):
        assert "stray" in _root()


@pytest.mark.usefixtures("strict")
def test_a_declared_read_returns_the_value_and_the_default_when_absent() -> None:
    root = _root()
    assert root.get("port") == 81
    assert ConfigView.root(_REGISTRY, {}).get("port", 80) == 80


@pytest.mark.usefixtures("lenient")
def test_an_undeclared_read_returns_the_value_when_the_switch_is_off() -> None:
    root = _root()
    assert root.get("stray") == 1
    assert "stray" in root


@pytest.mark.usefixtures("strict")
def test_a_sub_table_checks_its_reads_against_the_dotted_path() -> None:
    auth = _root().table("auth")
    assert auth.get("superuser") == "root"
    with pytest.raises(UndeclaredConfigKey, match=r"auth\.stray"):
        auth.get("stray")


@pytest.mark.usefixtures("strict")
def test_array_table_paths_render_with_brackets() -> None:
    (entry,) = _root().entries("runner")
    assert entry.get("name") == "a"
    with pytest.raises(UndeclaredConfigKey, match=r"runner\[\]\.stray"):
        entry.get("stray")


@pytest.mark.usefixtures("strict")
def test_a_read_beneath_an_open_table_is_allowed() -> None:
    flags = _root().table("flags")
    assert flags.get("anything") is None
    assert dict(flags.items()) == {"probe": True}


@pytest.mark.usefixtures("strict")
def test_iterating_a_closed_table_raises() -> None:
    with pytest.raises(UndeclaredConfigKey):
        list(_root().table("auth").items())


@pytest.mark.usefixtures("strict")
def test_a_value_that_is_not_a_table_reads_as_empty() -> None:
    root = ConfigView.root(_REGISTRY, {"auth": "oops", "runner": "oops"})
    assert root.table("auth").get("superuser") is None
    assert list(root.entries("runner")) == []


def test_the_registry_answers_what_it_declares() -> None:
    assert _REGISTRY.declares("port")
    assert _REGISTRY.declares("flags.deep.er")
    assert _REGISTRY.is_open("flags")
    assert not _REGISTRY.declares("stray")
    assert not _REGISTRY.is_open("auth")
