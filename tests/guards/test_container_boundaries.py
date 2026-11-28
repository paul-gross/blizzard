"""The registry's boundaries: dishka stays inside the translation package, and wiring modules stay apart.

Both gates run over the real source, where the second is vacuous until an area registers through the interface, and over
planted trees, where each helper fails the case it exists to catch."""

from pathlib import Path
from typing import Protocol

from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, HUB_LIFETIMES
from blizzard.foundation.registry.scopes import ScopeValues
from tests.support.container_guards import (
    CLI_ROOTS,
    HUB_ROOTS,
    alias_identity_violations,
    bag_violations,
    child_open_violations,
    default_factory_violations,
    dishka_importers,
    duplicate_owners,
    graph_builds,
    lifetime_violations,
    role_key_violations,
    root_registration_violations,
    wiring_boundary_violations,
)
from tests.support.repo_files import repo_root

_SRC = repo_root() / "src" / "blizzard"


class Engine:
    pass


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine


class Needs:
    def __init__(self, store: Store, limit: int = 3) -> None:
        self.store = store
        self.limit = limit


class IRole(Protocol):
    engine: Engine


def test_only_the_translation_package_imports_dishka() -> None:
    assert dishka_importers(_SRC) == []


def test_wiring_modules_import_no_other_wiring_and_no_adapter() -> None:
    assert wiring_boundary_violations(_SRC) == []


def test_no_module_opens_a_child_scope_except_through_open_child() -> None:
    assert child_open_violations(_SRC) == []
    tests = repo_root() / "tests"
    assert child_open_violations(tests, exempt=(("unit", "foundation", "registry"),)) == []


def test_the_child_open_gate_fails_a_planted_direct_open_and_passes_the_registry(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "hub/leak.py": "def go(owner):\n    return owner.open_child(lifetime=1, owner='t', values={})\n",
            "hub/door.py": "from blizzard.foundation.registry.scopes import open_child\nopen_child(p, lifetime=1)\n",
            "foundation/registry/scopes.py": "def go(parent):\n    return parent.open_child()\n",
        },
    )
    assert child_open_violations(root) == ["hub/leak.py:2"]


def _plant(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_the_dishka_gate_fails_a_planted_importer_and_passes_the_package(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "hub/leak.py": "import dishka\n",
            "hub/also.py": "from dishka.integrations import fastapi\n",
            "foundation/registry/internal/ok.py": "from dishka import Provider\n",
            "hub/api/findings.py": "from dishka.integrations.fastapi import FromDishka, inject_sync\n",
            "hub/api/provider.py": "from dishka.integrations.fastapi import FromDishka\nfrom dishka import Provider\n",
            "hub/api/whole.py": "from dishka.integrations import fastapi\n",
            "hub/domain/findings.py": "from dishka.integrations.fastapi import FromDishka\n",
        },
    )
    assert dishka_importers(root) == [
        "hub/also.py",
        "hub/api/provider.py",
        "hub/api/whole.py",
        "hub/domain/findings.py",
        "hub/leak.py",
    ]


def test_the_wiring_gate_catches_planted_cross_area_and_adapter_imports(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "findings/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.chunks.wiring import register\n"
                "from blizzard.chunks.internal.store import Store\n"
                "from blizzard.findings.service import Service\n"
            ),
            "chunks/wiring.py": "from blizzard.foundation.registry.interface import Registry\n",
            "chunks/service.py": "from blizzard.findings.wiring import register\n",
        },
    )
    assert wiring_boundary_violations(root) == [
        "findings/wiring.py: imports the adapter blizzard.chunks.internal.store",
        "findings/wiring.py: imports another area's wiring blizzard.chunks.wiring",
    ]


def test_the_wiring_gate_catches_a_domain_wiring_reaching_outside_the_domain(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "hub/domain/chunks/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.events.broker import EventBroker\n"
                "from blizzard.hub.work_sources.source import IWorkSourceRegistry\n"
                "from blizzard.hub.domain.chunks.service import Service\n"
                "from blizzard.hub.tenancy import STORE_UNAVAILABLE\n"
            ),
            "hub/events/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.events.broker import EventBroker\n"
            ),
        },
    )
    assert wiring_boundary_violations(root) == [
        "hub/domain/chunks/wiring.py: imports blizzard.hub.events.broker from outside the domain",
        "hub/domain/chunks/wiring.py: imports blizzard.hub.work_sources.source from outside the domain",
    ]


def test_the_wiring_gate_admits_an_adapter_wiring_its_own_internal_and_a_process_root(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "hub/store/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.store.internal.finding_store import FindingStore\n"
            ),
            "hub/store/other.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.store.internal.finding_store import FindingStore\n"
            ),
            "hub/domain/findings/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.store.internal.finding_store import FindingStore\n"
            ),
            "hub/process_root.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.hub.store.internal.finding_store import FindingStore\n"
                "from blizzard.hub.store.wiring import register\n"
            ),
        },
    )
    assert wiring_boundary_violations(root) == [
        "hub/domain/findings/wiring.py: imports the adapter blizzard.hub.store.internal.finding_store",
        "hub/store/other.py: imports the adapter blizzard.hub.store.internal.finding_store",
    ]
    assert wiring_boundary_violations(root, roots=frozenset()) == [
        "hub/domain/findings/wiring.py: imports the adapter blizzard.hub.store.internal.finding_store",
        "hub/process_root.py: imports the adapter blizzard.hub.store.internal.finding_store",
        "hub/process_root.py: imports another area's wiring blizzard.hub.store.wiring",
        "hub/store/other.py: imports the adapter blizzard.hub.store.internal.finding_store",
    ]


def test_the_wiring_gate_passes_a_wiring_module_that_imports_its_own_area(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "findings/wiring.py": (
                "from blizzard.foundation.registry.interface import Registry\n"
                "from blizzard.findings.service import Service\n"
            ),
        },
    )
    assert wiring_boundary_violations(root) == []


def _registry() -> RecordingRegistry:
    registry = RecordingRegistry()
    registry.service(Engine, lifetime=Lifetime.PROCESS)
    return registry


def test_graph_builds_fails_a_missing_dependency_and_passes_a_complete_graph() -> None:
    complete = _registry()
    complete.service(Store, lifetime=Lifetime.PROCESS)
    assert graph_builds(complete, HUB_LIFETIMES, ScopeValues()) == []
    incomplete = RecordingRegistry()
    incomplete.service(Store, lifetime=Lifetime.PROCESS)
    assert len(graph_builds(incomplete, HUB_LIFETIMES, ScopeValues())) == 1


def test_duplicate_owners_flags_a_second_owner_only() -> None:
    assert duplicate_owners(_registry()) == []
    twice = _registry()
    twice.service(Engine, lifetime=Lifetime.PROCESS)
    assert duplicate_owners(twice) == ["Engine has 2 owners"]


def test_lifetime_violations_flag_a_lifetime_off_the_process_path() -> None:
    assert lifetime_violations(_registry(), HUB_LIFETIMES) == []
    assert lifetime_violations(_registry(), HOST_LIFETIMES) == []
    off = RecordingRegistry()
    off.service(Engine, lifetime=Lifetime.RUNNER)
    assert len(lifetime_violations(off, HUB_LIFETIMES)) == 1


def test_alias_identity_flags_an_alias_whose_target_has_two_owners() -> None:
    registry = RecordingRegistry()
    engine = registry.service(Engine, lifetime=Lifetime.PROCESS)
    registry.alias(port(Engine), engine)
    assert alias_identity_violations(registry) == []
    clean = RecordingRegistry()
    owned = clean.service(Store, lifetime=Lifetime.PROCESS)
    clean.alias(port(IRole), owned)
    assert alias_identity_violations(clean) == []
    doubled = RecordingRegistry()
    first = doubled.service(Store, lifetime=Lifetime.PROCESS)
    doubled.service(Store, lifetime=Lifetime.PROCESS)
    doubled.alias(port(IRole), first)
    assert len(alias_identity_violations(doubled)) == 1


def test_role_keys_flag_two_roles_over_one_key() -> None:
    clean = RecordingRegistry()
    store = clean.service(Store, lifetime=Lifetime.PROCESS)
    clean.alias(port(IRole), store)
    assert role_key_violations(clean) == []
    clashing = RecordingRegistry()
    store = clashing.service(Store, lifetime=Lifetime.PROCESS)
    clashing.alias(port(IRole), store)
    clashing.alias(port(IRole), store)
    assert len(role_key_violations(clashing)) == 1


def test_default_factory_violations_flag_a_defaulted_constructor() -> None:
    assert default_factory_violations(_registry()) == []
    defaulted = _registry()
    defaulted.service(Needs, lifetime=Lifetime.PROCESS)
    assert default_factory_violations(defaulted) == ["Needs parameter 'limit' has a default"]


def test_no_module_names_a_services_bag() -> None:
    assert bag_violations(_SRC) == []


def test_the_bag_gate_fails_a_planted_bag_import_parameter_attribute_and_definition(tmp_path: Path) -> None:
    root = _plant(
        tmp_path,
        {
            "hub/api/planted.py": (
                "from blizzard.hub.api.deps import Services\n"
                "from blizzard.hub import composition\n"
                "def route(services: Services) -> composition.TenantServices: ...\n"
            ),
            "hub/domain/planted.py": "class HubCore: ...\ndef hub_services(): ...\n",
            "hub/api/clean.py": "from blizzard.hub.process_root import HubProcess\n",
        },
    )
    assert bag_violations(root) == [
        "hub/api/planted.py:1: names Services",
        "hub/api/planted.py:3: names Services",
        "hub/api/planted.py:3: names TenantServices",
        "hub/domain/planted.py:1: names HubCore",
        "hub/domain/planted.py:2: names hub_services",
    ]


def test_the_bag_gate_fails_a_bag_planted_in_a_hub_route_module(tmp_path: Path) -> None:
    route = Path("hub/api/chunks.py")
    planted = tmp_path / route
    planted.parent.mkdir(parents=True)
    planted.write_text((_SRC / route).read_text() + "\ndef planted(hub: Hub) -> ChunkReadStores: ...\n")
    lines = len(planted.read_text().splitlines())
    assert bag_violations(tmp_path) == [
        f"hub/api/chunks.py:{lines}: names ChunkReadStores",
        f"hub/api/chunks.py:{lines}: names Hub",
    ]


def test_no_hub_root_registers_anything_itself() -> None:
    assert root_registration_violations(_SRC) == []


def test_the_area_root_gate_fails_a_registration_planted_in_each_root(tmp_path: Path) -> None:
    planted = {
        Path("hub/process_root.py"): "def register_hub(registry):\n    registry.service(Store, lifetime=1)\n",
        Path("hub/app.py"): "def build(registry, ref):\n    registry.alias(port(IRole), ref)\n",
        Path("hub/composition.py"): "def build(registry):\n    registry.leaf(build_core, lifetime=1)\n",
    }
    assert set(planted) == set(HUB_ROOTS)
    root = _plant(tmp_path, {str(relative): text for relative, text in planted.items()})
    assert root_registration_violations(root) == [
        "hub/app.py:2: calls .alias(...)",
        "hub/composition.py:2: calls .leaf(...)",
        "hub/process_root.py:2: calls .service(...)",
    ]


def test_no_cli_root_registers_anything_itself() -> None:
    assert root_registration_violations(_SRC, CLI_ROOTS) == []


def test_the_area_root_gate_fails_a_registration_planted_in_each_cli_root(tmp_path: Path) -> None:
    planted = {
        Path("cli/command_root.py"): "def build(registry):\n    registry.context(port(IRole), lifetime=1)\n",
        Path("hub/cli/container_command.py"): "def build(registry):\n    registry.factory(build, lifetime=1)\n",
    }
    assert set(planted) == set(CLI_ROOTS)
    root = _plant(tmp_path, {str(relative): text for relative, text in planted.items()})
    assert root_registration_violations(root, CLI_ROOTS) == [
        "cli/command_root.py:2: calls .context(...)",
        "hub/cli/container_command.py:2: calls .factory(...)",
    ]
