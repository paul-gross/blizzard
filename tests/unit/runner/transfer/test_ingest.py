"""``RunnerIngest`` over in-memory ports: every path it resolves, probes, copies or makes goes through
:class:`~blizzard.runner.transfer.host_files.IHostFiles`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.secrets.value import SecretValue
from blizzard.runner.hub.sealed_tokens import FIRST_REVISION, SealedHubToken, sealed_name
from blizzard.runner.transfer.ingest import IngestOutcome, IngestRequest
from blizzard.runner.transfer.refusals import SourceTokenUnreadable, SourceUnsupported, TokenConflict
from blizzard.runner.transfer.rows import HostIngest
from blizzard.runner.transfer.source import SourceKind
from tests.support.runner_ingest_fakes import (
    HERE,
    HERE_URL,
    RUNNER,
    RUNNER_ID,
    T0,
    FakeCipher,
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


def _source_row(world: IngestWorld, token: str, *, key_id: str = "key-there") -> None:
    """The source store holds the runner's token sealed under ``key_id``."""
    world.ciphers.keys.setdefault(THERE, "key-there")
    sealed = FakeCipher(key_id).seal(SecretValue(token), name=sealed_name(RUNNER_ID), revision=FIRST_REVISION)
    world.stores.hub_tokens[_store_url(source_store(THERE))] = SealedHubToken(RUNNER_ID, FIRST_REVISION, sealed, T0)


def _opened_here(world: IngestWorld) -> str:
    row = world.stores.hub_tokens[HERE_URL]
    return FakeCipher(world.ciphers.keys[HERE]).open(row.secret()).expose()


def test_a_source_row_is_resealed_under_this_hosts_key_and_dropped_from_the_source() -> None:
    world = _host_directory()
    world.tokens.tokens.clear()
    _source_row(world, "sealed-token")

    world.ingest().run(IngestRequest(THERE, RUNNER))

    assert world.stores.hub_tokens[HERE_URL].sealed.key_id == "key-here"
    assert _opened_here(world) == "sealed-token"
    assert _store_url(source_store(THERE)) not in world.stores.hub_tokens


def test_a_source_row_wins_over_its_env_line() -> None:
    world = _host_directory()
    _source_row(world, "sealed-token")

    world.ingest().run(IngestRequest(THERE, RUNNER))

    assert _opened_here(world) == "sealed-token"


def test_an_env_only_source_is_sealed_under_this_hosts_key() -> None:
    world = _host_directory()

    world.ingest().run(IngestRequest(THERE, RUNNER))

    assert ("mint_key", HERE) in world.journal
    assert _opened_here(world) == "token"
    assert (THERE, hub_token_variable(RUNNER)) not in world.tokens.tokens


def test_a_source_row_that_does_not_open_refuses_before_anything_is_copied() -> None:
    world = _host_directory()
    _source_row(world, "sealed-token", key_id="key-lost")

    with pytest.raises(SourceTokenUnreadable, match="runner token set") as refused:
        world.ingest().run(IngestRequest(THERE, RUNNER))

    # This host's key resolves from the same environment, so the source's key is never asked for there.
    assert "BZ_HOST_SECRET_KEY" not in str(refused.value)
    assert not any(op[0] in ("copy_runner", "mint_key") for op in world.journal)
    assert HERE_URL not in world.stores.hub_tokens


def test_a_source_row_without_a_source_key_refuses() -> None:
    world = _host_directory()
    _source_row(world, "sealed-token")
    del world.ciphers.keys[THERE]

    with pytest.raises(SourceTokenUnreadable):
        world.ingest().run(IngestRequest(THERE, RUNNER))


def test_a_source_row_opens_under_the_shared_environment_key_and_is_sealed_under_this_hosts_own() -> None:
    world = _host_directory()
    _source_row(world, "sealed-token", key_id="key-env")
    del world.ciphers.keys[THERE]
    world.ciphers.environ_key = "key-env"
    world.ciphers.keys[HERE] = "key-here"

    world.ingest().run(IngestRequest(THERE, RUNNER))

    assert world.stores.hub_tokens[HERE_URL].sealed.key_id == "key-here"
    assert _opened_here(world) == "sealed-token"


def test_a_different_token_held_here_in_a_row_refuses() -> None:
    world = _host_directory()
    world.ciphers.keys[HERE] = "key-here"
    sealed = FakeCipher("key-here").seal(SecretValue("other"), name=sealed_name(RUNNER_ID), revision=FIRST_REVISION)
    world.stores.hub_tokens[HERE_URL] = SealedHubToken(RUNNER_ID, FIRST_REVISION, sealed, T0)

    with pytest.raises(TokenConflict, match="already holds a different hub token"):
        world.ingest().run(IngestRequest(THERE, RUNNER))
