"""The urgency's validation, its mark, the set's order, the settings, and the retire's refusals, as pure values."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.urgency.mark import ICON_MAX_BYTES, IconMediaType, IconShape, UrgencyMarkConflict
from blizzard.hub.domain.urgency.model import (
    Urgency,
    UrgencyEdit,
    UrgencyId,
    UrgencyInUse,
    UrgencyIsDefault,
    UrgencyMoveRefused,
    UrgencyMoveToSelf,
    UrgencyRetired,
    UrgencyShape,
    UrgencySlugTaken,
    UrgencyUnknown,
    require_retirable,
    require_slug_free,
)
from blizzard.hub.domain.urgency.order import Placed, UrgencyOrder, UrgencyOrderIncomplete, order_change
from blizzard.hub.domain.urgency.settings import TenantSettings

_AT = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_op", door=Door.API)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _new(shape: UrgencyShape, held: Sequence[ProjectColor] = ()) -> Urgency:
    return Urgency.new(shape, _CTX, siblings=lambda: held, pick=lambda colors: colors[0], at=_AT)[0]


def _field(exc: pytest.ExceptionInfo[ConfigFieldError]) -> str:
    return exc.value.field


# --- the record -------------------------------------------------------------------------------------------------


def test_a_create_defaults_its_name_to_the_slug_and_picks_the_least_used_color() -> None:
    held = [color for color in ProjectColor if color is not ProjectColor.SLATE]
    urgency = _new(UrgencyShape("urgent"), held)

    assert urgency.urgency_id.startswith("urg_")
    assert (urgency.slug, urgency.name, urgency.color, urgency.revision) == ("urgent", "urgent", ProjectColor.SLATE, 1)


def test_a_create_stores_a_hex_color_lowercased_and_writes_its_create_change() -> None:
    urgency, _, change = Urgency.new(
        UrgencyShape("urgent", name="Urgent", color="#AABBCC", emoji="🔥"),
        _CTX,
        siblings=tuple,
        pick=lambda colors: colors[0],
        at=_AT,
    )

    assert urgency.color == "#aabbcc"
    assert (change.record_kind, change.record_key, change.op, change.project_id) == (
        RecordKind.URGENCY,
        urgency.urgency_id,
        ChangeOp.CREATE,
        None,
    )
    assert [(d.field, d.new) for d in change.diff] == [
        ("slug", "urgent"),
        ("name", "Urgent"),
        ("color", "#aabbcc"),
        ("emoji", "🔥"),
    ]


@pytest.mark.parametrize("slug", ["", "Urgent", "-x", "a b", "x" * 65])
def test_a_slug_outside_the_grammar_is_refused(slug: str) -> None:
    with pytest.raises(ConfigFieldError) as exc:
        _new(UrgencyShape(slug))
    assert _field(exc) == "slug"


def test_a_slug_a_live_urgency_holds_is_taken() -> None:
    with pytest.raises(UrgencySlugTaken) as exc:
        require_slug_free("urgent", _new(UrgencyShape("urgent")))
    assert (exc.value.code, exc.value.field) == ("urgency_slug_taken", "slug")
    assert "enable" not in exc.value.message


def test_a_slug_a_retired_urgency_holds_is_taken_and_the_detail_names_enable() -> None:
    retired = replace(_new(UrgencyShape("urgent")), retired=True)

    with pytest.raises(UrgencySlugTaken) as exc:
        require_slug_free("urgent", retired)
    assert "retired" in exc.value.message
    assert "enable" in exc.value.message


@pytest.mark.parametrize(
    ("shape", "field"),
    [
        (UrgencyShape("u", name=None), "name"),
        (UrgencyShape("u", color=None), "color"),
        (UrgencyShape("u", name=""), "name"),
        (UrgencyShape("u", name=" padded"), "name"),
        (UrgencyShape("u", color="mauve"), "color"),
        (UrgencyShape("u", color="#abc"), "color"),
    ],
)
def test_an_explicit_null_a_blank_name_and_a_bad_color_are_refused(shape: UrgencyShape, field: str) -> None:
    with pytest.raises(ConfigFieldError) as exc:
        _new(shape)
    assert _field(exc) == field


def test_an_explicit_null_mark_on_a_create_means_none() -> None:
    urgency = _new(UrgencyShape("u", emoji=None, icon=None))
    assert (urgency.emoji, urgency.icon) == (None, None)


def test_an_edit_refuses_a_slug_and_explicit_nulls_on_name_and_color() -> None:
    urgency = _new(UrgencyShape("u"))
    for edit, field in (
        (UrgencyEdit(slug="v"), "slug"),
        (UrgencyEdit(name=None), "name"),
        (UrgencyEdit(color=None), "color"),
    ):
        with pytest.raises(ConfigFieldError) as exc:
            urgency.edit(edit, _CTX, if_match=None, at=_AT)
        assert _field(exc) == field


def test_an_edit_refuses_a_stale_revision_and_an_unchanged_edit_writes_nothing() -> None:
    urgency = _new(UrgencyShape("u", name="U"))
    with pytest.raises(ConfigRevisionConflict):
        urgency.edit(UrgencyEdit(name="V"), _CTX, if_match=7, at=_AT)
    assert urgency.edit(UrgencyEdit(name="U"), _CTX, if_match=1, at=_AT) is None


# --- the mark ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("emoji", ["🔥", "1️⃣", "1⃣", "#️⃣", "*️⃣", "👨‍👩‍👧", "🇺🇸", "!"])
def test_an_emoji_mark_is_admitted(emoji: str) -> None:
    assert _new(UrgencyShape("u", emoji=emoji)).emoji == emoji


@pytest.mark.parametrize(
    "emoji",
    ["", " ", "ab", "🔥a", "12", "1", "#", "🔥 🔥", "🔥\t", "\x07", "🔥" * 17],
)
def test_an_emoji_that_is_blank_long_spaced_controlled_lettered_or_a_bare_digit_is_refused(emoji: str) -> None:
    with pytest.raises(ConfigFieldError) as exc:
        _new(UrgencyShape("u", emoji=emoji))
    assert _field(exc) == "emoji"


@pytest.mark.parametrize(("data", "media_type"), [(_PNG, IconMediaType.PNG), (_WEBP, IconMediaType.WEBP)])
def test_a_png_or_webp_icon_is_stored_by_its_media_type_and_digest(data: bytes, media_type: IconMediaType) -> None:
    urgency, mark, _ = Urgency.new(
        UrgencyShape("u", icon=IconShape(media_type.value, _b64(data))),
        _CTX,
        siblings=tuple,
        pick=lambda colors: colors[0],
        at=_AT,
    )

    assert mark.icon is not None
    assert mark.icon.data == data
    assert urgency.icon == mark.icon.mark
    assert urgency.icon is not None
    assert urgency.icon.media_type is media_type
    assert len(urgency.icon.digest) == 64


@pytest.mark.parametrize(
    "icon",
    [
        IconShape("image/svg+xml", _b64(b"<svg xmlns='http://www.w3.org/2000/svg'/>")),
        IconShape("image/png", _b64(b"<svg xmlns='http://www.w3.org/2000/svg'/>")),
        IconShape("image/png", _b64(b"GIF89a" + b"\x00" * 10)),
        IconShape("image/png", _b64(_PNG + b"\x00" * ICON_MAX_BYTES)),
        IconShape("image/webp", _b64(_PNG)),
        IconShape("image/png", "not base64!"),
    ],
)
def test_an_svg_a_foreign_an_oversized_or_a_mislabelled_icon_is_refused(icon: IconShape) -> None:
    with pytest.raises(ConfigFieldError) as exc:
        _new(UrgencyShape("u", icon=icon))
    assert _field(exc) == "icon"


def test_both_marks_at_once_are_refused() -> None:
    with pytest.raises(UrgencyMarkConflict) as exc:
        _new(UrgencyShape("u", emoji="🔥", icon=IconShape("image/png", _b64(_PNG))))
    assert exc.value.code == "urgency_mark_conflict"


def test_an_edit_switches_marks_only_when_it_clears_the_old_one() -> None:
    urgency = _new(UrgencyShape("u", emoji="🔥"))
    icon = IconShape("image/png", _b64(_PNG))
    with pytest.raises(UrgencyMarkConflict):
        urgency.edit(UrgencyEdit(icon=icon), _CTX, if_match=None, at=_AT)

    edited = urgency.edit(UrgencyEdit(icon=icon, emoji=None), _CTX, if_match=None, at=_AT)

    assert edited is not None
    record, mark, _ = edited
    assert (record.emoji, record.revision, mark.keep) == (None, 2, False)
    assert mark.icon is not None


# --- the order --------------------------------------------------------------------------------------------------


def _order(*placed: tuple[str, float, bool]) -> UrgencyOrder:
    return UrgencyOrder.of((Placed(UrgencyId(slug), slug, pos, retired) for slug, pos, retired in placed), seq=5)


def test_the_order_is_by_position_ties_on_the_slug() -> None:
    order = _order(("b", 1.0, False), ("a", 1.0, False), ("c", 0.0, False))
    assert order.live_slugs() == ("c", "a", "b")


def test_a_create_lands_last_first_or_just_after_a_live_urgency() -> None:
    order = _order(("a", 0.0, False), ("b", 1.0, False))
    assert order.landing(None, last=True) == (2.0, ())
    assert order.landing(None, last=False) == (-1.0, ())
    assert order.landing(UrgencyId("a"), last=False) == (0.5, ())


def test_a_create_after_a_retired_or_unknown_urgency_is_refused() -> None:
    order = _order(("a", 0.0, True))
    with pytest.raises(UrgencyRetired):
        order.landing(UrgencyId("a"), last=False)
    with pytest.raises(UrgencyUnknown):
        order.landing(UrgencyId("z"), last=False)


def test_a_whole_order_ranks_the_live_set_and_writes_nothing_when_it_stands() -> None:
    order = _order(("a", 0.0, False), ("b", 1.0, False), ("r", 0.5, True))
    assert order.whole([UrgencyId("a"), UrgencyId("b")]) == ()

    facts = order.whole([UrgencyId("b"), UrgencyId("a")])

    assert order.after(facts).live_slugs() == ("b", "a")


@pytest.mark.parametrize(
    "ordered",
    [["a"], ["a", "b", "r"], ["a", "b", "a"]],
    ids=["leaves-one-out", "names-a-retired-one", "names-one-twice"],
)
def test_a_whole_order_that_is_incomplete_is_refused(ordered: list[str]) -> None:
    order = _order(("a", 0.0, False), ("b", 1.0, False), ("r", 0.5, True))
    with pytest.raises(UrgencyOrderIncomplete) as exc:
        order.whole([UrgencyId(u) for u in ordered])
    assert exc.value.code == "urgency_order_incomplete"


def test_a_whole_order_naming_an_unknown_urgency_is_refused() -> None:
    with pytest.raises(UrgencyUnknown):
        _order(("a", 0.0, False)).whole([UrgencyId("z")])


def test_a_move_lands_just_after_another_or_first_and_a_move_that_moves_nothing_writes_nothing() -> None:
    order = _order(("a", 0.0, False), ("b", 1.0, False), ("c", 2.0, False))
    assert order.after(order.move(UrgencyId("c"), after=None)).live_slugs() == ("c", "a", "b")
    assert order.after(order.move(UrgencyId("a"), after=UrgencyId("b"))).live_slugs() == ("b", "a", "c")
    assert order.move(UrgencyId("b"), after=UrgencyId("a")) == ()


def test_a_move_after_itself_or_of_a_retired_urgency_is_refused() -> None:
    order = _order(("a", 0.0, False), ("r", 1.0, True))
    with pytest.raises(ConfigFieldError):
        order.move(UrgencyId("a"), after=UrgencyId("a"))
    with pytest.raises(UrgencyRetired):
        order.move(UrgencyId("r"), after=None)
    with pytest.raises(UrgencyRetired):
        order.move(UrgencyId("a"), after=UrgencyId("r"))


def test_a_retired_urgency_keeps_its_place_and_returns_to_it() -> None:
    order = _order(("a", 0.0, False), ("r", 0.5, True), ("b", 1.0, False))
    revived = UrgencyOrder.of((replace(p, retired=False) for p in order.placed), order.seq)
    assert revived.live_slugs() == ("a", "r", "b")


def test_the_order_change_names_the_live_order_before_and_after_keyed_by_the_tenant() -> None:
    before = _order(("a", 0.0, False), ("b", 1.0, False))
    after = before.after(before.whole([UrgencyId("b"), UrgencyId("a")]))

    change = order_change(_CTX, "ten_1", before, after, at=_AT)

    assert (change.record_kind, change.record_key, change.project_id) == (RecordKind.URGENCY_ORDER, "ten_1", None)
    assert [(d.field, d.old, d.new) for d in change.diff] == [("order", ["a", "b"], ["b", "a"])]


# --- the settings -----------------------------------------------------------------------------------------------


def test_the_settings_seed_and_point_at_another_urgency_by_slug() -> None:
    standard, _ = Urgency.standard(_CTX, at=_AT)
    urgent = _new(UrgencyShape("urgent"))
    settings, seeded = TenantSettings.seed("ten_1", standard, _CTX, at=_AT)
    assert (settings.default_urgency_id, settings.revision, seeded.record_kind) == (
        standard.urgency_id,
        1,
        RecordKind.TENANT_SETTINGS,
    )
    assert settings.point_at(standard, held=standard, ctx=_CTX, if_match=None, at=_AT) is None

    pointed = settings.point_at(urgent, held=standard, ctx=_CTX, if_match=1, at=_AT)

    assert pointed is not None
    edited, change = pointed
    assert (edited.default_urgency_id, edited.revision) == (urgent.urgency_id, 2)
    assert [(d.field, d.old, d.new) for d in change.diff] == [("default_urgency", "standard", "urgent")]


# --- the retire's refusals --------------------------------------------------------------------------------------


def _retirable(
    *, default: bool = False, open_chunks: int = 0, key: str | None = None, target: Urgency | None = None
) -> Urgency | None:
    urgency = _new(UrgencyShape("urgent"))
    return require_retirable(
        urgency,
        default_id=urgency.urgency_id if default else UrgencyId("urg_other"),
        open_chunks=open_chunks,
        move_to_key=key,
        move_to=urgency if target is None and key == "self" else target,
        may_control=True,
    )


def test_a_retire_of_the_default_is_refused_first() -> None:
    with pytest.raises(UrgencyIsDefault):
        _retirable(default=True, open_chunks=3)


def test_a_retire_open_work_carries_naming_no_move_is_refused_counting_it() -> None:
    with pytest.raises(UrgencyInUse) as exc:
        _retirable(open_chunks=2)
    assert exc.value.open_chunks == 2


def test_a_retire_with_no_open_work_and_no_move_moves_nothing() -> None:
    assert _retirable() is None


def test_a_retire_moving_to_an_unknown_a_retired_or_itself_is_refused() -> None:
    with pytest.raises(UrgencyUnknown):
        _retirable(open_chunks=1, key="nope")
    with pytest.raises(UrgencyRetired):
        _retirable(open_chunks=1, key="old", target=replace(_new(UrgencyShape("old")), retired=True))
    with pytest.raises(UrgencyMoveToSelf):
        _retirable(open_chunks=1, key="self")


def test_a_retire_moving_open_work_needs_chunk_control_and_one_moving_none_does_not() -> None:
    urgency = _new(UrgencyShape("urgent"))
    target = _new(UrgencyShape("standard"))

    def retire(open_chunks: int) -> Urgency | None:
        return require_retirable(
            urgency,
            default_id=UrgencyId("urg_other"),
            open_chunks=open_chunks,
            move_to_key="standard",
            move_to=target,
            may_control=False,
        )

    with pytest.raises(UrgencyMoveRefused):
        retire(1)
    assert retire(0) == target


def test_every_refusal_outranks_the_permission_and_each_outranks_the_next() -> None:
    urgency = _new(UrgencyShape("urgent"))

    def retire(*, default: bool, open_chunks: int, key: str | None, target: Urgency | None) -> Urgency | None:
        return require_retirable(
            urgency,
            default_id=urgency.urgency_id if default else UrgencyId("urg_other"),
            open_chunks=open_chunks,
            move_to_key=key,
            move_to=target,
            may_control=False,
        )

    with pytest.raises(UrgencyIsDefault):
        retire(default=True, open_chunks=2, key="nope", target=None)
    with pytest.raises(UrgencyInUse):
        retire(default=False, open_chunks=2, key=None, target=None)
    with pytest.raises(UrgencyUnknown):
        retire(default=False, open_chunks=2, key="nope", target=None)
    with pytest.raises(UrgencyMoveToSelf):
        retire(default=False, open_chunks=2, key="urgent", target=urgency)
    with pytest.raises(UrgencyMoveRefused):
        retire(default=False, open_chunks=2, key="standard", target=_new(UrgencyShape("standard")))
