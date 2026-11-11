"""The transfer declarations carry every field of the host-file types they stand for, so an ingest drops none."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from blizzard.runner.composition import declared_subscription, declared_workspace
from blizzard.runner.config import HostWorkspace, SubscriptionDeclaration
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.host_file import host_workspace, subscription_declaration
from blizzard.runner.transfer.hosts import DeclaredSubscription, DeclaredWorkspace

#: A value for each field type the host-file types hold, none of them the field's default.
_WORKSPACE_VALUES: dict[str, Any] = {
    "id": "w-other",
    "root": "/srv/workspaces/other",
    "provider": "custom",
    "envs": ("e1", "e2"),
    "max_environments": 7,
    "repos": (WorkspaceRepo("repo", "https://example.test/repo.git"),),
    "base_branch": "trunk",
    "prompt": "inline prompt",
    "prompt_file": "prompts/standing.md",
    "prompt_package": "sample",
}
_SUBSCRIPTION_VALUES: dict[str, Any] = {
    "slug": "sub-1",
    "name": "Subscription one",
    "provider": "custom",
    "credentials_path": "/home/me/.creds.json",
    "sample_interval_seconds": 123,
    "usage_api_base": "https://usage.example.test",
}


def test_the_values_name_every_field_of_the_host_workspace() -> None:
    assert set(_WORKSPACE_VALUES) == {field.name for field in fields(HostWorkspace)}


def test_the_values_name_every_field_of_the_subscription_declaration() -> None:
    assert set(_SUBSCRIPTION_VALUES) == {field.name for field in fields(SubscriptionDeclaration)}


def test_a_workspace_survives_the_round_trip_through_its_declaration() -> None:
    workspace = HostWorkspace(**_WORKSPACE_VALUES)
    declared = declared_workspace(workspace, Path("/host"))
    assert isinstance(declared, DeclaredWorkspace)
    assert host_workspace(declared) == workspace


def test_a_workspace_declaration_resolves_its_root_against_the_declaring_host() -> None:
    declared = declared_workspace(HostWorkspace(id="w", root="relative"), Path("/host"))
    assert declared.effective_root == "/host/relative"


def test_a_subscription_survives_the_round_trip_through_its_declaration() -> None:
    subscription = SubscriptionDeclaration(**_SUBSCRIPTION_VALUES)
    declared = declared_subscription(subscription)
    assert isinstance(declared, DeclaredSubscription)
    assert subscription_declaration(declared) == subscription
