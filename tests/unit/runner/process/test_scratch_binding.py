from pathlib import Path

import pytest

from blizzard.runner.process.scratch_binding import BindingAction, decide_binding

OLD, NEW = Path("/scratch/old"), Path("/scratch/new")


@pytest.mark.parametrize(
    ("previous", "root", "active", "expected"),
    [
        (None, NEW, False, BindingAction.PUBLISH),
        (None, NEW, True, BindingAction.PUBLISH),
        (OLD, OLD, True, BindingAction.UNCHANGED),
        (OLD, OLD, False, BindingAction.UNCHANGED),
        (OLD, NEW, True, BindingAction.REFUSE_ACTIVE_LEASES),
        (OLD, NEW, False, BindingAction.RECONCILE_PREVIOUS_THEN_PUBLISH),
        (Path("relative/root"), NEW, False, BindingAction.REFUSE_INVALID_PREVIOUS),
        (Path("/scratch/\x00bad"), NEW, False, BindingAction.REFUSE_INVALID_PREVIOUS),
        (Path("relative/root"), Path("relative/root"), False, BindingAction.REFUSE_INVALID_PREVIOUS),
    ],
)
def test_decide_binding(previous: Path | None, root: Path, active: bool, expected: BindingAction) -> None:
    assert decide_binding(previous, root, active=active) is expected
