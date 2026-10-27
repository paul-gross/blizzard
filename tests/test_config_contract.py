"""The committed config documents against the registries they are built from.

``contracts/config/<daemon>.json`` is the sorted rendering of the daemon's config registry; a registry edit with no
regenerated document, or a document deleted from the tree, turns this red."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import click
import pytest

from blizzard.foundation.config_keys import OPEN_TABLE, ConfigRegistry
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.runner.cli import runner
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.harness.wiring import HarnessSections, declared
from blizzard.runner.hub.internal.token_file import HubTokenFile
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


def _host() -> dict:
    return document_from_registry(HOST_REGISTRY)


def _row(path: str) -> dict:
    return next(row for row in _host()["paths"] if row["path"] == path)


def test_the_host_names_every_file_the_layout_lists_each_with_a_writer() -> None:
    for path in (".env", "runners/<name>", "worker-settings.json", "plugins/blizzard-heartbeat.ts"):
        assert _row(path)["written_by"], path
    assert _row("opencode-worker-config.json")["written_by"] == ["init"]
    assert _row(".env")["mode"] == "0600"


def test_every_writer_is_a_runner_command() -> None:
    commands = set(runner.list_commands(click.Context(runner)))
    for row in _host()["paths"]:
        assert set(row["written_by"]) <= commands, row["path"]


def test_init_scaffolds_exactly_the_harness_files_the_contract_names_with_writer_init(tmp_path: Path) -> None:
    for declaration, section in declared(HarnessSections.defaults()):
        declaration.scaffold_runtime(section, tmp_path)
    written = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    named = {row["path"] for row in _host()["paths"] if row["written_by"] == ["init"]}
    assert written == named


def test_a_token_file_write_has_the_env_sections_name_and_mode(tmp_path: Path) -> None:
    env = _host()["env"]
    HubTokenFile.of(tmp_path, HOST_TOKEN_VARIABLE).write("t")
    path = tmp_path / env["file"]
    assert f"{stat.S_IMODE(path.stat().st_mode):04o}" == env["mode"]
    assert env["atomic"] is True


def test_the_documents_name_rule_derives_what_hub_token_variable_does() -> None:
    variable = next(v for v in _host()["env"]["variables"] if v["name_rule"])
    rule = variable["name_rule"]
    assert variable["name"] == rule["prefix"] + "<NAME>"
    for name in ("r-claude", "a.b c_d", "MiXed9-x"):
        derived = rule["prefix"] + re.sub(rule["replace_pattern"], rule["replacement"], name.upper())
        assert derived == hub_token_variable(name)


def test_a_fixed_variable_has_no_name_rule_and_the_hub_declares_no_env() -> None:
    assert next(v for v in _host()["env"]["variables"] if v["name"] == HOST_TOKEN_VARIABLE)["name_rule"] is None
    assert document_from_registry(HUB_REGISTRY)["env"] is None


#: The modules under ``src/blizzard`` that write ``.env`` or create ``runners/<name>``, and the verbs the contract names
#: for them. A module that starts writing either, or a writer verb the contract drops, turns this red.
_ENV_WRITER_MODULES = {
    "runner/cli/runners.py": {"add"},
    "runner/cli/runtime.py": {"init", "host"},
    "runner/ingest.py": {"ingest"},
    "runner/hub/bootstrap.py": {"init"},
    "runner/hub/host_bootstrap.py": {"host"},
}
_ENV_WRITE_MARKERS = ("write_runner_token(", ".write(issued.token)", "tokens.write(", "HubTokenFile.of(")
_RUNNER_DIR_MODULES = {"runner/cli/runners.py": {"add"}, "runner/ingest.py": {"ingest"}}


def _modules_containing(markers: tuple[str, ...]) -> set[str]:
    src = repo_root() / "src" / "blizzard"
    return {
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if "internal/token_file.py" not in str(p) and any(m in p.read_text() for m in markers)
    }


def test_the_env_writers_are_the_modules_that_write_the_env_file() -> None:
    # composition.py only wires the writer into ingest and the daemon; it is not a verb of its own.
    found = _modules_containing(_ENV_WRITE_MARKERS) - {"runner/composition.py"}
    assert found == set(_ENV_WRITER_MODULES)
    verbs = set().union(*_ENV_WRITER_MODULES.values())
    assert verbs == set(_row(".env")["written_by"])


def test_the_runner_directory_writers_are_the_modules_that_create_it() -> None:
    found = _modules_containing(("RUNNERS_DIRNAME / ",)) - {"runner/host_layout.py"}
    assert found == set(_RUNNER_DIR_MODULES)
    assert set().union(*_RUNNER_DIR_MODULES.values()) == set(_row("runners/<name>")["written_by"])
