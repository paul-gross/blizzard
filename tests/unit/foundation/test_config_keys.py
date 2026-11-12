from __future__ import annotations

import pytest

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    INTEGER,
    OPEN_TABLE,
    STRING,
    TABLE,
    ConfigKey,
    ConfigRegistry,
    ConfigView,
    Lifetime,
    UndeclaredConfigKey,
)

_REGISTRY = ConfigRegistry(
    "demo",
    (
        ConfigKey("port", INTEGER, default=80, lifetime=Lifetime.BOOT),
        ConfigKey("flags", OPEN_TABLE, lifetime=Lifetime.BOOT),
        ConfigKey("auth", TABLE),
        ConfigKey("auth.superuser", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("runner", ARRAY_OF_TABLES),
        ConfigKey("runner[].name", STRING, required=True, lifetime=Lifetime.PASS),
    ),
)
_DOCUMENT = {
    "port": 81,
    "stray": 1,
    "flags": {"probe": True},
    "auth": {"superuser": "root", "stray": 1},
    "runner": [{"name": "a", "stray": 1}],
}


def _root(*, strict: bool = True) -> ConfigView:
    return ConfigView.root(_REGISTRY, _DOCUMENT, strict=strict)


def test_an_undeclared_read_names_the_path_and_the_registry() -> None:
    with pytest.raises(UndeclaredConfigKey, match=r"'stray'.*demo") as raised:
        _root().get("stray")
    assert (raised.value.path, raised.value.registry) == ("stray", "demo")


def test_a_presence_test_of_an_undeclared_key_raises() -> None:
    with pytest.raises(UndeclaredConfigKey):
        assert "stray" in _root()


def test_a_declared_read_returns_the_value_and_the_default_when_absent() -> None:
    root = _root()
    assert root.get("port") == 81
    assert ConfigView.root(_REGISTRY, {}, strict=True).get("port", 80) == 80


def test_an_undeclared_read_returns_the_value_when_the_root_is_lenient() -> None:
    root = _root(strict=False)
    assert root.get("stray") == 1
    assert "stray" in root
    assert root.table("auth").get("stray") == 1
    (entry,) = root.entries("runner")
    assert entry.get("stray") == 1


def test_a_sub_table_checks_its_reads_against_the_dotted_path() -> None:
    auth = _root().table("auth")
    assert auth.get("superuser") == "root"
    with pytest.raises(UndeclaredConfigKey, match=r"auth\.stray"):
        auth.get("stray")


def test_array_table_paths_render_with_brackets() -> None:
    (entry,) = _root().entries("runner")
    assert entry.get("name") == "a"
    with pytest.raises(UndeclaredConfigKey, match=r"runner\[\]\.stray"):
        entry.get("stray")


def test_a_read_beneath_an_open_table_is_allowed() -> None:
    flags = _root().table("flags")
    assert flags.get("anything") is None
    assert dict(flags.items()) == {"probe": True}


def test_iterating_a_closed_table_raises() -> None:
    with pytest.raises(UndeclaredConfigKey):
        list(_root().table("auth").items())


def test_a_value_that_is_not_a_table_reads_as_empty() -> None:
    root = ConfigView.root(_REGISTRY, {"auth": "oops", "runner": "oops"}, strict=True)
    assert root.table("auth").get("superuser") is None
    assert list(root.entries("runner")) == []


def test_the_registry_answers_what_it_declares() -> None:
    assert _REGISTRY.declares("port")
    assert _REGISTRY.declares("flags.deep.er")
    assert _REGISTRY.is_open("flags")
    assert not _REGISTRY.declares("stray")
    assert not _REGISTRY.is_open("auth")


def test_states_and_value_resolve_a_nested_path() -> None:
    root = _root()
    assert root.states("auth.superuser")
    assert root.value("auth.superuser") == "root"
    absent = ConfigView.root(_REGISTRY, {"auth": {}}, strict=True)
    assert not absent.states("auth.superuser")
    assert absent.value("auth.superuser") is None


def test_states_and_value_answer_for_an_element_views_own_keys() -> None:
    element = next(_root().entries("runner"))
    assert element.states("name")
    assert element.value("name") == "a"


def test_value_returns_the_whole_list_table_or_array() -> None:
    root = _root()
    assert root.value("flags") == {"probe": True}
    assert root.value("runner") == [{"name": "a", "stray": 1}]


def test_a_flag_held_key_is_not_stated() -> None:
    from blizzard.foundation.flags import Flags

    registry = ConfigRegistry("demo", (ConfigKey("held", INTEGER, flag="f"),))
    flags = Flags(known=frozenset({"f"}), retired=frozenset(), values={"f": False}, published=frozenset())
    root = ConfigView.root(registry, {"held": 1}, strict=True).holding(flags)
    assert not root.states("held")
    assert root.value("held") is None


def test_a_value_key_with_no_lifetime_is_refused_naming_it() -> None:
    with pytest.raises(ValueError, match=r"^port: a value key declares its lifetime$"):
        ConfigRegistry("demo", (ConfigKey("port", INTEGER),))


def test_an_open_table_holds_a_value_and_so_declares_a_lifetime() -> None:
    with pytest.raises(ValueError, match="flags: a value key declares its lifetime"):
        ConfigRegistry("demo", (ConfigKey("flags", OPEN_TABLE),))


@pytest.mark.parametrize("kind", [TABLE, ARRAY_OF_TABLES])
def test_a_grouping_key_with_a_lifetime_is_refused_naming_it(kind: str) -> None:
    with pytest.raises(ValueError, match=r"^auth: a grouping key carries no lifetime$"):
        ConfigRegistry("demo", (ConfigKey("auth", kind, lifetime=Lifetime.BOOT),))


@pytest.mark.parametrize("lifetime", [Lifetime.PASS, Lifetime.SESSION, Lifetime.IMMUTABLE])
def test_a_variable_on_a_key_that_is_not_boot_is_refused_naming_it(lifetime: Lifetime) -> None:
    with pytest.raises(ValueError, match=r"^port: only a boot key is supplied by an environment variable$"):
        ConfigRegistry("demo", (ConfigKey("port", INTEGER, lifetime=lifetime, env="BZ_PORT"),))


def test_a_boot_key_may_name_its_variable() -> None:
    registry = ConfigRegistry("demo", (ConfigKey("port", INTEGER, lifetime=Lifetime.BOOT, env="BZ_PORT"),))
    assert registry.key("port") == ConfigKey("port", INTEGER, lifetime=Lifetime.BOOT, env="BZ_PORT")
