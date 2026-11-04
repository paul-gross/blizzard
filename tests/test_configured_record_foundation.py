"""The shared mechanics accept kind-owned identity and field projections."""

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeName, ForgeSlug
from blizzard.hub.domain.config.lifecycle import (
    field_changes,
    merge_labels,
    require_revision,
    slug_left_for,
    transition,
)
from blizzard.hub.domain.kernel.unset import UNSET

pytestmark = pytest.mark.unit


def test_revision_and_transition() -> None:
    require_revision(None, 2, kind="link", key="p/t")
    with pytest.raises(ConfigRevisionConflict, match="link p/t is at revision 2"):
        require_revision(1, 2, kind="link", key="p/t")
    assert transition(False, False) is None
    assert transition(True, True) is None
    assert transition(False, True) == ChangeOp.RETIRE
    assert transition(True, False) == ChangeOp.ENABLE


def test_projection_and_change_row() -> None:
    changes = field_changes([("b", None, 2), ("same", None, None), ("a", 1, None)])
    assert changes == (FieldChange("b", None, 2), FieldChange("a", 1, None))
    at = datetime(2025, 1, 1, tzinfo=UTC)
    row = ConfigChange.of(
        ChangeContext("actor", Door.APPLY, "apply"),
        RecordKind.PROJECT_SOURCE_LINK,
        "target",
        3,
        ChangeOp.EDIT,
        changes,
        at,
        project_id="project",
    )
    assert (row.record_key, row.project_id, row.revision, row.diff, row.apply_id) == (
        "target",
        "project",
        3,
        changes,
        "apply",
    )


def test_labels_and_slug_left() -> None:
    assert merge_labels("old", "Name", UNSET, UNSET, parse_slug=ForgeSlug.parse, parse_name=ForgeName.parse) == (
        "old",
        "Name",
    )
    assert merge_labels("old", "Name", "new", "Other", parse_slug=ForgeSlug.parse, parse_name=ForgeName.parse) == (
        "new",
        "Other",
    )
    with pytest.raises(ConfigFieldError, match="slug: must not be null"):
        merge_labels("old", "Name", None, UNSET, parse_slug=ForgeSlug.parse, parse_name=ForgeName.parse)
    with pytest.raises(ConfigFieldError):
        merge_labels("old", "Name", UNSET, "", parse_slug=ForgeSlug.parse, parse_name=ForgeName.parse)
    assert slug_left_for("old", "new") == "old"
    assert slug_left_for("old", "old") is None
