"""Translate host registry paths to the runner record's whole-value keys."""

from __future__ import annotations

from blizzard.foundation.config_keys import ARRAY_OF_TABLES, ConfigRegistry, Lifetime


def runner_keys(registry: ConfigRegistry) -> dict[str, Lifetime]:
    """Return the record-relative pass/session keys, preserving arrays as whole values.

    Registry paths inside an array describe its members, not independently writable records.
    A deprecated declaration is not a second writable key.
    """
    prefix = "runner[]."
    arrays = {key.path: key for key in registry.keys if key.path.startswith(prefix) and key.type == ARRAY_OF_TABLES}
    result: dict[str, Lifetime] = {}
    for key in registry.keys:
        if not key.path.startswith(prefix) or key.deprecated or key.lifetime not in (Lifetime.PASS, Lifetime.SESSION):
            continue
        path = key.path
        for array in arrays:
            if path.startswith(array + "[]."):
                path = array
                break
        relative = path.removeprefix(prefix)
        lifetime = key.lifetime
        assert lifetime is not None
        previous = result.setdefault(relative, lifetime)
        if previous != key.lifetime:
            raise ValueError(f"{path}: array members have different settings lifetimes")
    return result
