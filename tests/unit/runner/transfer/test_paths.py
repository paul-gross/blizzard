"""Path decisions need no filesystem and retain ordered copies."""

from pathlib import Path

from blizzard.runner.transfer.paths import rebase_path_keys


def test_rebase_paths_and_ordered_duplicate_copies() -> None:
    root, destination = Path("/source"), Path("/destination/r")
    nested = ("harness", "config_dir")
    paths: dict[tuple[str, ...], Path] = {
        nested: Path("runners/r/missing"),
        ("inside",): root / "shared",
        ("outside",): Path("/other/file"),
    }
    replacements, copies = rebase_path_keys(
        paths, (nested, ("absent",), ("inside",), ("outside",), nested), root, Path("runners/r"), destination
    )
    assert replacements == {nested: destination / "missing", ("inside",): destination / "shared"}
    assert copies == (
        (root / "runners/r/missing", destination / "missing"),
        (root / "shared", destination / "shared"),
        (root / "runners/r/missing", destination / "missing"),
    )


def test_only_initial_runner_prefix_is_removed() -> None:
    root = Path("/source")
    result, copies = rebase_path_keys(
        {("key",): Path("other/runners/r/file")}, (("key",),), root, Path("runners/r"), Path("/target")
    )
    assert result == {("key",): Path("/target/other/runners/r/file")}
    assert copies == ((root / "other/runners/r/file", Path("/target/other/runners/r/file")),)
