"""The parse boundary for ``.github/workflows/*.yml`` — the typed shape of the
workflow keys the CI-workflow tests read, and the one loader that decodes a
workflow file into it.

PyYAML's default (YAML 1.1) resolver reads the bare ``on:`` key as the boolean
``True``; the loader keeps it the string ``"on"``, so a trigger reads as
``workflow["on"]``.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, TypedDict

import yaml

type Scalar = str | bool | int | float | None

Step = TypedDict(
    "Step",
    {
        "name": str,
        "id": str,
        "uses": str,
        "run": str,
        "with": dict[str, Scalar],
        "env": dict[str, Scalar],
        "working-directory": str,
    },
)

Job = TypedDict(
    "Job",
    {
        "name": str,
        "needs": list[str],
        "if": str,
        "uses": str,
        "with": dict[str, Scalar],
        "env": dict[str, Scalar],
        "permissions": dict[str, Scalar],
        "outputs": dict[str, Scalar],
        "steps": list[Step],
    },
)


class CallOutput(TypedDict):
    value: str


class WorkflowCall(TypedDict):
    outputs: dict[str, CallOutput]


class Branches(TypedDict):
    branches: list[str]


class Trigger(TypedDict):
    push: Branches
    workflow_call: WorkflowCall
    workflow_dispatch: None
    schedule: list[dict[str, str]]


class Workflow(TypedDict):
    on: Trigger
    permissions: dict[str, Scalar]
    jobs: dict[str, Job]


_BOOL_TAG = "tag:yaml.org,2002:bool"


class _WorkflowLoader(yaml.SafeLoader):
    """A safe loader whose bool resolver skips words starting with ``o``/``O`` — ``on``/``off`` stay strings."""

    yaml_implicit_resolvers: ClassVar = {
        first: [(tag, regexp) for tag, regexp in resolvers if not (first in "oO" and tag == _BOOL_TAG)]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def load_workflow(path: Path) -> Workflow:
    workflow: Workflow = yaml.load(path.read_text(), Loader=_WorkflowLoader)
    return workflow
