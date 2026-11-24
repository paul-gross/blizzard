"""The settable-key rule: which keys a settings write may name, and how the rest are refused."""

from __future__ import annotations

import pytest

from blizzard.foundation.config_keys import INTEGER, TABLE, ConfigKey, ConfigRegistry, Lifetime
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.hub.settings.footing import (
    SettingNotLive,
    SettingUnknown,
    export_document,
    require_settable,
    setting_keys,
)

REGISTRY = ConfigRegistry(
    name="hub",
    keys=(
        ConfigKey("live", INTEGER, default=1, lifetime=Lifetime.PASS),
        ConfigKey("zlive", INTEGER, default=1, lifetime=Lifetime.PASS),
        ConfigKey("boot", INTEGER, default=1, lifetime=Lifetime.BOOT),
        ConfigKey("old", INTEGER, deprecated=True, lifetime=Lifetime.PASS),
        ConfigKey("group", TABLE),
        ConfigKey("group.child", INTEGER, default=1, lifetime=Lifetime.PASS),
    ),
)


def test_a_pass_key_is_settable() -> None:
    require_settable(REGISTRY, {"live", "group.child"})
    assert "group" not in setting_keys(REGISTRY)


@pytest.mark.parametrize("key", ["missing", "old", "group", "group.child[].x"])
def test_an_undeclared_deprecated_or_grouping_key_is_unknown(key: str) -> None:
    with pytest.raises(SettingUnknown) as refusal:
        require_settable(REGISTRY, {key})
    assert refusal.value.key == key


def test_a_boot_key_is_not_live() -> None:
    with pytest.raises(SettingNotLive) as refusal:
        require_settable(REGISTRY, {"boot"})
    assert refusal.value.key == "boot"


def test_the_first_offending_key_in_sorted_order_is_refused() -> None:
    with pytest.raises(SettingNotLive):
        require_settable(REGISTRY, {"missing", "boot", "zlive"})


def test_export_leaves_out_every_key_apply_would_refuse() -> None:
    entries = {
        "live": Entry(5),
        "zlive": Entry(1, at_default=True),
        "boot": Entry(2),
        "old": Entry(3),
        "group": Entry({"child": 4}),
        "group.child": Entry(6),
        "missing": Entry(7),
    }
    document = export_document(REGISTRY, entries)
    assert document == {"live": 5, "group": {"child": 6}}
    require_settable(REGISTRY, {"live", "group.child"})
