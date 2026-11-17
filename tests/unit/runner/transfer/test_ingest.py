"""``RunnerIngest`` over in-memory ports: every path it resolves, probes, copies or makes goes through
:class:`~blizzard.runner.transfer.host_files.IHostFiles`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.transfer.ingest import IngestOutcome, IngestRequest
from blizzard.runner.transfer.refusals import SourceUnsupported
from blizzard.runner.transfer.rows import HostIngest
from blizzard.runner.transfer.source import SourceKind
from tests.support.runner_ingest_fakes import (
    HERE,
    HERE_URL,
    RUNNER,
    RUNNER_ID,
    T0,
    IngestWorld,
    ingest_world,
    source_store,
)

THERE = Path("/hosts/there")
LINK = Path("/links/there")


def _store_url(store: Path) -> str:
    return f"fake://{store}"


def _host_directory(*, store: bool = True, runners: tuple[str, ...] = (RUNNER_ID,)) -> IngestWorld:
    """A graduated source ``THERE``, named through the symlink ``LINK``, its store holding ``runners``."""
    world = ingest_world({LINK: SourceKind.HOST_DIRECTORY, THERE: SourceKind.HOST_DIRECTORY})
    world.files.links[LINK] = THERE
    world.tokens.tokens[THERE, hub_token_variable(RUNNER)] = "token"
    if store:
        world.files.files.add(source_store(THERE))
        world.stores.named[_store_url(source_store(THERE))] = runners
    return world


def _resuming(world: IngestWorld) -> None:
    """This host's store already holds the runner from an ingest of ``THERE``."""
    world.stores.named[HERE_URL] = (RUNNER_ID,)
    world.stores.ingests[HERE_URL] = HostIngest(RUNNER_ID, str(THERE), T0, T0)


def test_the_source_is_resolved_through_the_port_and_the_resolved_path_drives_the_ingest() -> None:
    world = _host_directory(runners=(RUNNER_ID, "rn_other"))

    outcome = world.ingest().run(IngestRequest(LINK, RUNNER))

    assert outcome == IngestOutcome(RUNNER_ID, RUNNER, resumed=False)
    assert world.journal[0] == ("resolved", LINK)
    assert ("hold", HERE) in world.journal and ("hold", THERE) in world.journal
    assert ("open_host", THERE) in world.journal
    assert ("copy_runner", str(THERE)) in world.journal
    assert world.stores.ingests[HERE_URL].source == str(THERE)


def test_a_source_resolving_to_this_host_is_refused_before_any_hold() -> None:
    world = ingest_world({LINK: SourceKind.HOST_DIRECTORY})
    world.files.links[LINK] = HERE

    with pytest.raises(SourceUnsupported, match="is this host"):
        world.ingest().run(IngestRequest(LINK, RUNNER))

    assert world.journal == [("resolved", LINK)]


def test_an_ungraduated_source_without_a_store_is_refused() -> None:
    world = ingest_world({THERE: SourceKind.UNGRADUATED})

    with pytest.raises(
        SourceUnsupported, match=re.escape(f"the source holds no store at {THERE / 'data' / 'runner.db'}")
    ):
        world.ingest().run(IngestRequest(THERE, RUNNER))

    assert not any(op[0] == "copy_of" for op in world.journal)


def test_a_missing_source_store_is_refused_on_a_first_ingest() -> None:
    world = _host_directory(store=False)

    with pytest.raises(SourceUnsupported, match="the source holds no store at"):
        world.ingest().run(IngestRequest(THERE, RUNNER))


def test_a_missing_source_store_completes_an_ingest_it_resumes() -> None:
    world = _host_directory(store=False)
    _resuming(world)

    outcome = world.ingest().run(IngestRequest(THERE, RUNNER))

    assert outcome == IngestOutcome(RUNNER_ID, RUNNER, resumed=True)
    assert not any(op[0] in ("copy_of", "set_aside") for op in world.journal)


def test_an_emptied_source_store_is_set_aside_when_present() -> None:
    world = _host_directory(runners=())
    _resuming(world)

    outcome = world.ingest().run(IngestRequest(THERE, RUNNER))

    assert outcome.resumed and outcome.set_aside
    assert ("set_aside", source_store(THERE)) in world.journal


def test_the_set_aside_of_an_emptied_store_is_skipped_when_its_store_is_absent() -> None:
    world = _host_directory()
    world.stores.on_remove = lambda _url: world.files.files.discard(source_store(THERE))

    outcome = world.ingest().run(IngestRequest(THERE, RUNNER))

    assert ("remove_runner_rows", _store_url(source_store(THERE))) in world.journal
    assert outcome.set_aside == ()
    assert not any(op[0] == "set_aside" for op in world.journal)


def test_each_path_is_copied_by_its_kind_before_the_runner_directory_and_its_table() -> None:
    world = _host_directory(runners=(RUNNER_ID, "rn_other"))
    directory, file, absent = THERE / "transcripts", THERE / "prompts" / "standing.md", THERE / "missing"
    world.files.directories.add(directory)
    world.files.files.add(file)
    world.sources.path_keys_set = {("a",): directory, ("b",): file, ("c",): absent}

    world.ingest().run(IngestRequest(THERE, RUNNER))

    runner_dir = HERE / "runners" / RUNNER
    configured = [op for op in world.journal if op[0] in ("copy_tree", "copy_file", "make_directory", "append_runner")]
    assert configured == [
        ("copy_tree", directory, runner_dir / "transcripts"),
        ("copy_file", file, runner_dir / "prompts" / "standing.md"),
        ("make_directory", runner_dir),
        ("append_runner", HERE),
    ]
    assert runner_dir / "missing" not in world.files.files | world.files.directories
