"""No-code evidence binds every part of a resolved declaration."""

from dataclasses import replace

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.no_code import finished_repos, no_code_marker, no_code_repos


def test_no_code_evidence_matches_only_its_declaration() -> None:
    declaration = {"repo": "org/repo", "record": "record-1", "base_branch": "main", "branch": "work", "commit": "abc"}
    name, data = no_code_marker(declaration)
    marker = StoredArtifact(ArtifactKind.ASSET, name, data, None, None, "a", "c", "n", "land", 1)
    assert no_code_repos([marker], [declaration]) == {"org/repo"}
    for key in declaration:
        assert not no_code_repos([marker], [{**declaration, key: "changed"}])
    assert not no_code_repos([replace(marker, data="{}")], [declaration])
    assert not no_code_repos([marker], [{"repo": "org/repo", "branch": "work", "commit": "abc"}])


def test_finished_repos_unions_landed_and_current_no_code_in_their_own_scopes() -> None:
    declaration = {"repo": "org/no-code", "record": "r", "base_branch": "main", "branch": "work", "commit": "abc"}
    name, data = no_code_marker(declaration)
    marker = StoredArtifact(ArtifactKind.ASSET, name, data, None, None, "a", "c", "n", "land", 1)
    landed = replace(marker, name="merged/org/landed", data="abc")
    blank = replace(marker, name="merged/org/blank", data="  ")
    outside = replace(marker, name="merged/org/outside", data="def")

    assert finished_repos([landed, blank, marker], [marker, outside], [declaration]) == {
        "org/landed",
        "org/no-code",
    }
    assert finished_repos([marker], [], [declaration]) == frozenset()
    assert finished_repos([], [marker], [{**declaration, "commit": "changed"}]) == frozenset()
