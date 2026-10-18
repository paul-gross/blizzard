"""The committed config documents against the registries they are built from.

``contracts/config/<daemon>.json`` is the sorted rendering of the daemon's config registry; a registry edit with no
regenerated document, or a document deleted from the tree, turns this red."""

from __future__ import annotations

import json

import pytest

from blizzard.foundation.config_keys import OPEN_TABLE, ConfigRegistry
from blizzard.tools.config_contract import document_from_registry
from blizzard.tools.contracts import CONFIGS
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("name", "registry"), CONFIGS, ids=[name for name, _ in CONFIGS])
def test_the_committed_document_is_what_the_registry_renders(name: str, registry: ConfigRegistry) -> None:
    committed = repo_root() / "contracts" / "config" / f"{name}.json"
    assert committed.is_file(), f"{committed} is missing — run `mise run export-contracts` and commit it"
    assert json.loads(committed.read_text()) == json.loads(json.dumps(document_from_registry(registry)))


@pytest.mark.parametrize(("name", "registry"), CONFIGS, ids=[name for name, _ in CONFIGS])
def test_flags_is_one_open_table_with_no_keys_beneath_it(name: str, registry: ConfigRegistry) -> None:
    document = document_from_registry(registry)
    flags = [key for key in document["keys"] if key["path"] == "flags" or key["path"].startswith("flags.")]
    assert [(key["path"], key["type"]) for key in flags] == [("flags", OPEN_TABLE)]


@pytest.mark.parametrize(("name", "registry"), CONFIGS, ids=[name for name, _ in CONFIGS])
def test_a_deprecated_key_names_a_declared_replacement_or_none(name: str, registry: ConfigRegistry) -> None:
    document = document_from_registry(registry)
    declared = {key["path"] for key in document["keys"]}
    for key in document["keys"]:
        if key["replaced_by"] is not None:
            assert key["deprecated"], key["path"]
            assert key["replaced_by"] in declared, f"{key['path']} -> {key['replaced_by']}"
