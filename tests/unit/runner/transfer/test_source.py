"""Source classification uses observed values and preserves operator-facing refusals."""

import pytest

from blizzard.runner.transfer.refusals import SourceUnsupported
from blizzard.runner.transfer.source import SQLITE_HEADER, DirectoryState, SourceKind, classify_source


@pytest.mark.parametrize(
    ("directory", "is_file", "header", "expected"),
    [
        (DirectoryState.GRADUATED, False, b"", SourceKind.HOST_DIRECTORY),
        (DirectoryState.UNGRADUATED, False, b"", SourceKind.UNGRADUATED),
        (None, True, SQLITE_HEADER, SourceKind.ARCHIVE),
        (DirectoryState.GRADUATED, True, SQLITE_HEADER, SourceKind.HOST_DIRECTORY),
    ],
)
def test_classification(directory: DirectoryState | None, is_file: bool, header: bytes, expected: SourceKind) -> None:
    assert (
        classify_source(
            "./original", directory=directory, is_file=is_file, header=header, host_config_filename="host.toml"
        )
        == expected
    )


@pytest.mark.parametrize(
    ("directory", "is_file", "header", "message"),
    [
        (DirectoryState.UNINITIALISED, False, b"", "./original holds no host.toml, so it is not a host directory"),
        (
            None,
            True,
            b"not sqlite",
            "./original is not a SQLite store, so it is not an archive `runner store reset` wrote",
        ),
        (None, False, b"", "./original does not exist"),
    ],
)
def test_refusals(directory: DirectoryState | None, is_file: bool, header: bytes, message: str) -> None:
    with pytest.raises(SourceUnsupported) as exc:
        classify_source(
            "./original", directory=directory, is_file=is_file, header=header, host_config_filename="host.toml"
        )
    assert str(exc.value) == message
