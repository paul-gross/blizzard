from pathlib import Path

from blizzard.runner.harness.binary_identity import BinaryIdentity
from blizzard.runner.harness.internal.binary_identity import ToolBinaryIdentity
from blizzard.runner.process.child_env import ChildEnv


def test_identity_uses_tool_path_and_resolved_target(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("version one")
    target.chmod(0o755)
    (tmp_path / "tool").symlink_to(target)
    identity = ToolBinaryIdentity(ChildEnv((("PATH", str(tmp_path)),)))

    stat = target.stat()
    assert identity.identify("tool") == BinaryIdentity(target, stat.st_size, stat.st_mtime_ns)
    assert identity.identify(str(target)) == BinaryIdentity(target, stat.st_size, stat.st_mtime_ns)
    assert ToolBinaryIdentity(ChildEnv((("PATH", ""),))).identify("tool") is None


def test_identity_changes_with_file_and_missing_binary_is_not_reused(tmp_path: Path) -> None:
    target = tmp_path / "tool"
    target.write_text("one")
    target.chmod(0o755)
    identity = ToolBinaryIdentity(ChildEnv((("PATH", str(tmp_path)),)))
    first = identity.identify("tool")
    target.write_text("different size")
    assert first is not None
    assert identity.identify("tool") != first
    target.unlink()
    assert identity.identify("tool") is None
