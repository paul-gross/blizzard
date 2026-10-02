"""Readers of the published trace dictionary (``contracts/traces/dictionary.json``) shared by the
trace-contract unit tier and the e2e shape matcher, so neither restates what the dictionary says."""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from tests.repo_files import repo_root

CONTRACT_DIR = repo_root() / "contracts" / "traces"

#: The span names the dictionary publishes as a template over a node or session name, by role.
PARAMETERIZED_NAMES = {
    "step": "step <node>",
    "gate": "gate <node>",
    "runner/worker": "worker <node>",
    "runner/invocation": "invoke_agent <session>",
}

BARE_NAMES = {"invoke_agent": "runner/invocation"}

#: The dictionary's attribute type for each Python scalar an OTLP value decodes to.
OTLP_TYPE_OF = {str: "string", bool: "bool", int: "int", float: "double"}


@cache
def dictionary() -> dict[str, Any]:
    return json.loads((CONTRACT_DIR / "dictionary.json").read_text())


def role_of_name(name: str) -> str:
    """The dictionary role of a span name; a name the dictionary does not publish raises ``StopIteration``."""
    for role, template in PARAMETERIZED_NAMES.items():
        if name.startswith(template.split(" ", 1)[0] + " "):
            return role
    if name in BARE_NAMES:
        return BARE_NAMES[name]
    return next(entry["role"] for entry in dictionary()["spans"] if entry["name"] == name)


def scope_of_role(role: str) -> str:
    return next(entry["scope"] for entry in dictionary()["spans"] if entry["role"] == role)


def otlp_type(value: object) -> str:
    """The dictionary type of a decoded attribute value."""
    if isinstance(value, list):
        assert all(isinstance(v, str) for v in value)
        return "string[]"
    return OTLP_TYPE_OF[type(value)]


def required_by_role() -> dict[str, list[str]]:
    """Each non-optional attribute name, with the roles it must ride bar its ``optional_on``."""
    return {
        a["name"]: [role for role in a["on"] if role not in a.get("optional_on", ())]
        for a in dictionary()["attributes"]
        if not a["optional"]
    }
