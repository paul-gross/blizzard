"""Helpers for the host config keys tests, shared with other test modules."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    BOOLEAN,
    INTEGER,
    NUMBER,
    OPEN_TABLE,
    STRING_LIST,
    STRING_OR_LIST,
    TABLE,
    ConfigRegistry,
)

#: Values a key of each type takes that the loader accepts; a key the loader checks harder has its own below.
_SAMPLE_BY_TYPE: dict[str, object] = {
    BOOLEAN: True,
    INTEGER: 1,
    NUMBER: 0.5,
    STRING_LIST: ["x"],
    STRING_OR_LIST: "https://a.example",
}


_SAMPLE_BY_PATH: dict[str, object] = {
    "loop": "stepped",
    "trusted_proxies": ["127.0.0.1"],
    "workspace[].provider": "basic",
    "workspace[].prompt_package": "",
    "workspace[].repo[].name": "repo",
    "runner[].subscription": "sub",
    "subscription[].slug": "sub",
    "subscription[].usage_api_base": "http://usage.test",
    "runner[].projects[].workspace": "ws",
    "workspace[].id": "ws",
    "runner[].harness.autonomy": "dangerous",
    "runner[].harness.config_dir": "/opt/harness",
    "runner[].opencode.models.supported": ["openai/*"],
    "runner[].worker.path_prepend": ["/opt/bin"],
    "runner[].worker.shell": "/opt/bin/sh",
    "tools.path_prepend": ["/opt/tools"],
    "runner[].throttle.budget.usd": 0.25,
    "runner[].throttle.schedule.days": ["mon"],
    "runner[].throttle.schedule.hours": ["19:00-08:00"],
    "runner[].throttle.on_decline": "release",
    "runner[].throttle.on_usage_limit": "release",
    "tracing.platform_sample_ratio": 0.5,
    "tracing.worker_programs": False,
}


def document_carrying_every_key(registry: ConfigRegistry, overrides: dict[str, object] | None = None) -> dict[str, Any]:
    """A host document holding every declared key that is not deprecated, nested as the file nests it."""
    document: dict[str, Any] = {}
    for key in registry.keys:
        if key.deprecated:
            continue
        *parents, leaf = key.path.split(".")
        table = document
        for parent in parents:
            name = parent.removesuffix("[]")
            if parent.endswith("[]"):
                entries = table.setdefault(name, [{}])
                table = entries[0]
            else:
                table = table.setdefault(name, {})
        if key.type in (TABLE, OPEN_TABLE):
            table.setdefault(leaf, {})
        elif key.type == ARRAY_OF_TABLES:
            table.setdefault(leaf.removesuffix("[]"), [{}])
        else:
            table[leaf] = {**_SAMPLE_BY_PATH, **(overrides or {})}.get(key.path, _SAMPLE_BY_TYPE.get(key.type, "x"))
    document["flags"] = {"probe": True}
    return document
