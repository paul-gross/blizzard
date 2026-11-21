"""Pyright holds the registry interface's typing: a valid binding passes, and a missing, incompatible, role-mismatched,
or invariance-violating one is rejected."""

import json
import subprocess
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[4]

_PRELUDE = dedent(
    """
    from typing import Protocol

    from blizzard.foundation.registry.interface import Lifetime, Port, Ref, Registry, port


    class IReadFindingRepository(Protocol):
        def read(self) -> str: ...


    class IWriteFindingRepository(Protocol):
        def write(self, value: str) -> None: ...


    class ReadStore:
        def read(self) -> str:
            return ""


    class WriteStore:
        def write(self, value: str) -> None:
            return None


    __all__ = ["Lifetime", "Port", "Ref", "Registry", "port"]

    READ_FINDINGS: Port[IReadFindingRepository] = port(IReadFindingRepository)
    WRITE_FINDINGS: Port[IWriteFindingRepository] = port(IWriteFindingRepository)


    def register(registry: Registry, *, findings: Ref[IWriteFindingRepository]) -> None:
        registry.alias(WRITE_FINDINGS, findings)
    """
)


def _diagnostics(tmp_path: Path, body: str) -> list[tuple[int, str]]:
    """Run strict pyright over a fixture: ``(line, rule)`` for each error, lines counted within ``body``."""
    source = _PRELUDE + body
    offset = _PRELUDE.count("\n")
    (tmp_path / "fixture.py").write_text(source)
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps(
            {
                "typeCheckingMode": "strict",
                "extraPaths": [str(ROOT / "src")],
                "include": ["fixture.py"],
                "pythonVersion": "3.12",
            }
        )
    )
    result = subprocess.run(
        ["uv", "run", "pyright", "--outputjson", "--project", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    return [
        (item["range"]["start"]["line"] + 1 - offset, item.get("rule", ""))
        for item in report["generalDiagnostics"]
        if item["severity"] == "error"
    ]


def test_a_valid_binding_is_clean(tmp_path: Path) -> None:
    body = dedent(
        """
        def root(registry: Registry) -> None:
            writes = registry.service(WriteStore, lifetime=Lifetime.TENANT)
            register(registry, findings=writes)
        """
    )
    assert _diagnostics(tmp_path, body) == []


def test_a_missing_binding_is_rejected(tmp_path: Path) -> None:
    body = dedent(
        """
        def root(registry: Registry) -> None:
            register(registry)
        """
    )
    assert _diagnostics(tmp_path, body) == [(3, "reportCallIssue")]


def test_an_incompatible_binding_is_rejected(tmp_path: Path) -> None:
    body = dedent(
        """
        def root(registry: Registry) -> None:
            reads = registry.service(ReadStore, lifetime=Lifetime.TENANT)
            register(registry, findings=reads)
        """
    )
    assert _diagnostics(tmp_path, body) == [(4, "reportArgumentType")]


def test_an_alias_onto_a_different_role_is_rejected(tmp_path: Path) -> None:
    body = dedent(
        """
        def root(registry: Registry, writes: Ref[IWriteFindingRepository]) -> None:
            registry.alias(READ_FINDINGS, writes)
        """
    )
    assert _diagnostics(tmp_path, body) == [(3, "reportArgumentType")]


def test_a_port_is_invariant(tmp_path: Path) -> None:
    body = dedent(
        """
        def wants_object(candidate: Port[object]) -> None: ...


        wants_object(READ_FINDINGS)
        """
    )
    assert _diagnostics(tmp_path, body) == [(5, "reportArgumentType")]
