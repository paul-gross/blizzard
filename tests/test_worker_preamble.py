"""The runner spawn-preamble renderer (issues #17, #103, #149, unit tier).

The pure three-layer composition the core hands the adapter as ``prompt_prefix``: the
blizzard preamble, the operator's workspace prompt, and a machine-local info table. A
fresh render (``prior=None``) emits all three unchanged; a resumed one (#149) compares
the standing layers against the last-sent fingerprint and announces what moved."""

from __future__ import annotations

import hashlib

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.preamble import (
    DEFAULT_BLIZZARD_PREAMBLE,
    RESUME_BLIZZARD_UNCHANGED,
    RESUME_STANDING_UNCHANGED,
    RESUME_UPDATED_NOTICE,
    RESUME_WORKSPACE_UNCHANGED,
    RESUME_WORKSPACE_WITHDRAWN,
    Preamble,
    PreambleFingerprint,
    resume_cross_node,
)


def _render(
    workspace_prompt: str,
    envs: list[AcquiredEnvironment],
    *,
    runner_prompt: str = "",
    prior: PreambleFingerprint | None = None,
    node: str | None = None,
    prior_node: str | None = None,
) -> str:
    return Preamble.of(
        runner_prompt=runner_prompt,
        workspace_prompt=workspace_prompt,
        environments=envs,
        lease_id="lease_1",
        runner_id="runner-local",
        chunk_id="ch_1",
        prior=prior,
        node=node,
        prior_node=prior_node,
    ).text


def _fingerprint(
    workspace_prompt: str,
    envs: list[AcquiredEnvironment],
    *,
    runner_prompt: str = "",
) -> PreambleFingerprint:
    return Preamble.of(
        runner_prompt=runner_prompt,
        workspace_prompt=workspace_prompt,
        environments=envs,
        lease_id="lease_1",
        runner_id="runner-local",
        chunk_id="ch_1",
    ).fingerprint


_ENVS = [AcquiredEnvironment("r1", "/ws/r1")]

#: The layer-3 table every path owes the worker in full, at this helper's fixed identity.
_TABLE = (
    "| Field | Value |\n"
    "|-------|-------|\n"
    "| runner id | `runner-local` |\n"
    "| chunk id | `ch_1` |\n"
    "| lease id | `lease_1` |\n"
    "| environment name | `r1` |\n"
    "| environment workdir | `/ws/r1` |"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.unit
def test_single_env_table_carries_identity_and_the_one_env() -> None:
    out = _render("You are a fleet worker.", [AcquiredEnvironment("r1", "/ws/r1")])
    # The blizzard preamble leads (the baked default, since runner_prompt is unset).
    assert out.startswith(DEFAULT_BLIZZARD_PREAMBLE)
    # The workspace prose layers between the blizzard preamble and the facts table.
    assert "\n\nYou are a fleet worker.\n\n" in out
    # Machine-local identity rows.
    assert "| runner id | `runner-local` |" in out
    assert "| chunk id | `ch_1` |" in out
    assert "| lease id | `lease_1` |" in out
    # The single environment appears as a name/workdir pair.
    assert "| environment name | `r1` |" in out
    assert "| environment workdir | `/ws/r1` |" in out


@pytest.mark.unit
def test_multi_env_table_names_every_held_environment() -> None:
    out = _render(
        "prose",
        [AcquiredEnvironment("r1", "/ws/r1"), AcquiredEnvironment("r2", "/ws/r2")],
    )
    # Both held environments appear — never just the first (issue #17).
    assert "| environment name | `r1` |" in out
    assert "| environment workdir | `/ws/r1` |" in out
    assert "| environment name | `r2` |" in out
    assert "| environment workdir | `/ws/r2` |" in out
    # One row-pair per env: two name rows, two workdir rows.
    assert out.count("| environment name |") == 2
    assert out.count("| environment workdir |") == 2


@pytest.mark.unit
def test_empty_workspace_prompt_omits_that_layer() -> None:
    # Absent/empty workspace prompt omits layer 2 — the blizzard preamble and the
    # table still compose, back to back, with no workspace-prose layer between them.
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out == f"{DEFAULT_BLIZZARD_PREAMBLE}\n\n| Field | Value |\n|-------|-------|\n" + (
        "| runner id | `runner-local` |\n"
        "| chunk id | `ch_1` |\n"
        "| lease id | `lease_1` |\n"
        "| environment name | `r1` |\n"
        "| environment workdir | `/ws/r1` |"
    )


@pytest.mark.unit
def test_baked_default_used_when_runner_prompt_unset() -> None:
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out.startswith(DEFAULT_BLIZZARD_PREAMBLE)
    # The command surface is a reference table (blizzard#341): one row per worker verb,
    # each pointing at its own `--help` for the detail the preamble no longer carries.
    assert "| `work-items <chunk-id>` |" in out
    assert "| `chunk history`" in out
    assert "| `artifact …`" in out
    assert '| `ask "<question>"`' in out
    assert "blizzard runner heartbeat" in out
    assert "blizzard runner session-end" in out
    # The worker surface is discovered from the CLI's own audience labels plus per-command
    # `--help`, not restated here — the preamble names the rule, the CLI owns the detail.
    assert "labeled **Worker:**" in out
    assert "--help" in out


@pytest.mark.unit
def test_baked_default_opens_with_a_title_and_declares_its_scope() -> None:
    # Issue #147: layer 1 is a titled document, not an untitled prose blob — and it says
    # what it covers, so an operator's layer-2 prompt has no reason to re-establish it.
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")])
    assert out.startswith("# Blizzard fleet worker\n")
    assert "holds identically in every deployment" in out
    # Names layer 2 as the home for deployment-specific prose, without supplying any —
    # matched on collapsed whitespace, since the 120-column wrap may split the phrase.
    assert "workspace prompt" in " ".join(out.split())


@pytest.mark.unit
def test_runner_prompt_overrides_the_baked_default() -> None:
    out = _render("", [AcquiredEnvironment("r1", "/ws/r1")], runner_prompt="Custom blizzard framing.")
    assert out.startswith("Custom blizzard framing.\n\n")
    assert DEFAULT_BLIZZARD_PREAMBLE not in out


@pytest.mark.unit
def test_runner_prompt_layers_ahead_of_workspace_prompt_ahead_of_table() -> None:
    out = _render("Workspace-specific prose.", [AcquiredEnvironment("r1", "/ws/r1")], runner_prompt="Blizzard prose.")
    assert out == (
        "Blizzard prose.\n\n"
        "Workspace-specific prose.\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| runner id | `runner-local` |\n"
        "| chunk id | `ch_1` |\n"
        "| lease id | `lease_1` |\n"
        "| environment name | `r1` |\n"
        "| environment workdir | `/ws/r1` |"
    )


# --- Issue #149: what a resumed spawn sends -----------------------------------------


@pytest.mark.unit
def test_resume_with_unchanged_standing_prose_collapses_both_layers() -> None:
    """AC2/AC6: nothing moved, so the two standing layers become one line — and layer 3
    still arrives in full, freshly minted lease id and all."""
    prose = "Workspace-specific prose."
    prior = _fingerprint(prose, _ENVS, runner_prompt="Blizzard prose.")

    out = _render(prose, _ENVS, runner_prompt="Blizzard prose.", prior=prior)

    assert out == f"{RESUME_STANDING_UNCHANGED}\n\n{_TABLE}"
    # Neither layer's prose is re-sent...
    assert "Blizzard prose." not in out
    assert prose not in out
    # ...and nothing claims an update, because nothing was updated.
    assert RESUME_UPDATED_NOTICE not in out
    # The collapse line still introduces the table layer 1's prose would have introduced.
    assert "facts table below" in RESUME_STANDING_UNCHANGED


@pytest.mark.unit
def test_same_node_resume_is_byte_identical_to_the_plain_collapse() -> None:
    """blizzard#340: a resume by the node that produced the previous turn renders exactly
    the notice a node-blind render always produced — the variant never leaks in."""
    prose = "Workspace-specific prose."
    prior = _fingerprint(prose, _ENVS, runner_prompt="Blizzard prose.")

    out = _render(prose, _ENVS, runner_prompt="Blizzard prose.", prior=prior, node="build", prior_node="build")

    assert out == f"{RESUME_STANDING_UNCHANGED}\n\n{_TABLE}"


@pytest.mark.unit
def test_cross_node_resume_names_both_nodes_and_keeps_the_layers_collapsed() -> None:
    """blizzard#340: `build` resuming a session whose previous turn was `verify` — the
    common shape when two nodes share a session pool. The role-change line leads; the
    standing layers still collapse rather than re-send."""
    prose = "Workspace-specific prose."
    prior = _fingerprint(prose, _ENVS, runner_prompt="Blizzard prose.")

    out = _render(prose, _ENVS, runner_prompt="Blizzard prose.", prior=prior, node="build", prior_node="verify")

    cross = resume_cross_node(node="build", prior_node="verify")
    assert out == f"{cross}\n\n{RESUME_STANDING_UNCHANGED}\n\n{_TABLE}"
    assert "`verify` node-step" in out
    # Wrap-insensitive: the 120-column wrap may split the phrase across lines.
    assert "this turn works `build`" in " ".join(out.split())
    assert "Blizzard prose." not in out
    assert prose not in out


@pytest.mark.unit
def test_cross_node_resume_with_no_workspace_prompt_still_announces_the_role_change() -> None:
    """blizzard#340: a deployment with no layer 2 collapses to the blizzard-only line —
    which must not read as continuity either, so the role-change line rides it the same."""
    prior = _fingerprint("", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("", _ENVS, runner_prompt="Blizzard prose.", prior=prior, node="build", prior_node="verify")

    cross = resume_cross_node(node="build", prior_node="verify")
    assert out == f"{cross}\n\n{RESUME_BLIZZARD_UNCHANGED}\n\n{_TABLE}"


@pytest.mark.unit
def test_resume_with_an_unknown_prior_node_falls_back_to_the_plain_collapse() -> None:
    """blizzard#340: with no recorded node for the previous turn there is nothing to name,
    so the safe direction is the wording that claims only what is known."""
    prose = "Workspace-specific prose."
    prior = _fingerprint(prose, _ENVS, runner_prompt="Blizzard prose.")

    out = _render(prose, _ENVS, runner_prompt="Blizzard prose.", prior=prior, node="build", prior_node=None)

    assert out == f"{RESUME_STANDING_UNCHANGED}\n\n{_TABLE}"


@pytest.mark.unit
def test_a_changed_standing_layer_carries_the_role_change_line_too() -> None:
    """blizzard#340 in the updated-notice path: the role-change line still leads the render
    — ahead of the announcement, whose "what follows supersedes" scope covers the layers it
    introduces and not the role change."""
    prior = _fingerprint("Old policy.", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("New policy.", _ENVS, runner_prompt="Blizzard prose.", prior=prior, node="build", prior_node="verify")

    cross = resume_cross_node(node="build", prior_node="verify")
    assert out == f"{cross}\n\n{RESUME_UPDATED_NOTICE}\n\n{RESUME_BLIZZARD_UNCHANGED}\n\nNew policy.\n\n{_TABLE}"


@pytest.mark.unit
def test_a_cross_node_resume_with_no_recorded_fingerprint_still_announces_the_role_change() -> None:
    """blizzard#340 on the full-render path: a resume whose fingerprint was never recorded
    — a crash between the spawn and its fingerprint write — re-sends every layer, and a
    recorded prior node that differs still leads it with the role-change line."""
    prose = "Workspace-specific prose."

    out = _render(prose, _ENVS, runner_prompt="Blizzard prose.", prior=None, node="build", prior_node="verify")

    cross = resume_cross_node(node="build", prior_node="verify")
    assert out == f"{cross}\n\nBlizzard prose.\n\n{prose}\n\n{_TABLE}"


@pytest.mark.unit
def test_resume_with_a_changed_workspace_prompt_announces_and_sends_layer_two() -> None:
    """AC3, the common case: a live ``PUT /api/workspace-prompt`` between two spawns of one
    session. The new prose arrives in full behind an explicit announcement — never in the
    same position looking like the block the worker was handed several spawns ago."""
    prior = _fingerprint("Old policy.", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("New policy.", _ENVS, runner_prompt="Blizzard prose.", prior=prior)

    assert out == f"{RESUME_UPDATED_NOTICE}\n\n{RESUME_BLIZZARD_UNCHANGED}\n\nNew policy.\n\n{_TABLE}"
    assert "Old policy." not in out
    # Layer 1 collapsed, so ITS collapse line is what introduces the facts table — the
    # per-layer rule, on the branch a banner-only pointer would leave unintroduced.
    assert "Blizzard prose." not in out
    assert "facts table below" in RESUME_BLIZZARD_UNCHANGED


@pytest.mark.unit
def test_resume_with_a_changed_runner_prompt_announces_and_sends_layer_one() -> None:
    """The mirror of the case above — layer 1's configured door, reachable across the
    runner restart a ``runner_prompt`` change requires."""
    prose = "Workspace-specific prose."
    prior = _fingerprint(prose, _ENVS, runner_prompt="Old blizzard prose.")

    out = _render(prose, _ENVS, runner_prompt="New blizzard prose.", prior=prior)

    assert out == f"{RESUME_UPDATED_NOTICE}\n\nNew blizzard prose.\n\n{RESUME_WORKSPACE_UNCHANGED}\n\n{_TABLE}"
    assert "Old blizzard prose." not in out
    assert prose not in out


@pytest.mark.unit
def test_resume_across_a_packaged_default_upgrade_re_sends_layer_one() -> None:
    """Layer 1's other door: ``runner_prompt`` unset, so a packaged preamble upgrade
    moves the prose — the digest must cover the resolved layer text, not the raw
    (empty) knob, or a genuinely-changed preamble would go unnoticed."""
    prior = PreambleFingerprint(blizzard=_sha("the preamble as an older release shipped it"), workspace=_sha("prose"))

    out = _render("prose", _ENVS, runner_prompt="", prior=prior)

    assert out.startswith(f"{RESUME_UPDATED_NOTICE}\n\n{DEFAULT_BLIZZARD_PREAMBLE}\n\n")
    assert RESUME_WORKSPACE_UNCHANGED in out
    assert out.endswith(_TABLE)


@pytest.mark.unit
def test_resume_with_both_layers_changed_sends_both_in_full() -> None:
    prior = _fingerprint("Old policy.", _ENVS, runner_prompt="Old blizzard prose.")

    out = _render("New policy.", _ENVS, runner_prompt="New blizzard prose.", prior=prior)

    assert out == f"{RESUME_UPDATED_NOTICE}\n\nNew blizzard prose.\n\nNew policy.\n\n{_TABLE}"


@pytest.mark.unit
def test_resume_after_the_workspace_prompt_is_withdrawn_says_so() -> None:
    """A change whose new text is nothing. Silence would read as "unchanged" to a worker
    still holding the withdrawn policy, so the withdrawal is stated outright."""
    prior = _fingerprint("Policy that is going away.", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("", _ENVS, runner_prompt="Blizzard prose.", prior=prior)

    assert out == f"{RESUME_UPDATED_NOTICE}\n\n{RESUME_BLIZZARD_UNCHANGED}\n\n{RESUME_WORKSPACE_WITHDRAWN}\n\n{_TABLE}"
    assert "Policy that is going away." not in out


@pytest.mark.unit
def test_resume_with_a_whitespace_only_workspace_replace_announces_nothing() -> None:
    """The digest is taken post-``strip()``, so a whitespace-only ``PUT`` is not a change —
    a false "instructions updated" alarm is exactly what would make the real signal
    untrustworthy."""
    prior = _fingerprint("", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("   \n\t  \n", _ENVS, runner_prompt="Blizzard prose.", prior=prior)

    assert RESUME_UPDATED_NOTICE not in out
    assert RESUME_WORKSPACE_WITHDRAWN not in out


@pytest.mark.unit
def test_resume_with_no_workspace_prompt_collapses_layer_one_alone() -> None:
    """A deployment that never set a workspace prompt has only layer 1 to collapse: saying
    its absent layer 2 is "unchanged" would be noise the fresh render never emits either.
    The facts-table pointer still travels with layer 1."""
    prior = _fingerprint("", _ENVS, runner_prompt="Blizzard prose.")

    out = _render("", _ENVS, runner_prompt="Blizzard prose.", prior=prior)

    assert out == f"{RESUME_BLIZZARD_UNCHANGED}\n\n{_TABLE}"
    assert RESUME_WORKSPACE_UNCHANGED not in out


@pytest.mark.unit
def test_resume_still_carries_this_attempts_freshly_minted_lease_id() -> None:
    """AC6 on the *elided* path — the one where a stale table would be easiest to ship.
    Layer 3 is re-rendered per attempt and never compared, so the resumed render carries
    this spawn's lease id and no trace of the previous attempt's."""
    prior = _fingerprint("prose", _ENVS, runner_prompt="Blizzard prose.")

    out = Preamble.of(
        runner_prompt="Blizzard prose.",
        workspace_prompt="prose",
        environments=_ENVS,
        lease_id="lease_this_attempt",
        runner_id="runner-local",
        chunk_id="ch_1",
        prior=prior,
    ).text

    assert "| lease id | `lease_this_attempt` |" in out
    assert "lease_1" not in out
    # The whole table, not just the lease row.
    assert "| environment name | `r1` |" in out
    assert "| environment workdir | `/ws/r1` |" in out


@pytest.mark.unit
def test_fingerprint_is_stable_across_identical_inputs() -> None:
    first = _fingerprint("prose", _ENVS, runner_prompt="blizzard")
    second = _fingerprint("prose", [AcquiredEnvironment("r9", "/ws/r9")], runner_prompt="blizzard")
    # Layer 3 is not a fingerprint input: a different held environment and a different
    # lease do not move the standing digests.
    assert first == second


@pytest.mark.unit
def test_fingerprint_moves_when_either_resolved_layer_moves() -> None:
    base = _fingerprint("prose", _ENVS, runner_prompt="blizzard")

    assert _fingerprint("other prose", _ENVS, runner_prompt="blizzard").workspace != base.workspace
    assert _fingerprint("other prose", _ENVS, runner_prompt="blizzard").blizzard == base.blizzard
    assert _fingerprint("prose", _ENVS, runner_prompt="other blizzard").blizzard != base.blizzard
    assert _fingerprint("prose", _ENVS, runner_prompt="other blizzard").workspace == base.workspace


@pytest.mark.unit
def test_fingerprint_digests_the_resolved_layers_not_the_raw_inputs() -> None:
    """The single statement of the digest's source, pinned: layer 1 digests the resolved
    preamble (the baked default when the knob is unset), layer 2 the stripped prose."""
    unset = _fingerprint("  prose  ", _ENVS, runner_prompt="")

    assert unset.blizzard == _sha(DEFAULT_BLIZZARD_PREAMBLE)
    assert unset.workspace == _sha("prose")
    # An override resolves to its own text, and whitespace around it is not part of it.
    assert _fingerprint("", _ENVS, runner_prompt="  custom  ").blizzard == _sha("custom")


@pytest.mark.unit
def test_fingerprint_is_the_same_whether_the_render_elided_or_not() -> None:
    """The elided path must record the digest of the prose the session *holds*, not of
    the banner it just emitted, or the next comparison would find a false mismatch."""
    fresh = Preamble.of(
        runner_prompt="Blizzard prose.",
        workspace_prompt="prose",
        environments=_ENVS,
        lease_id="lease_1",
        runner_id="runner-local",
        chunk_id="ch_1",
    )
    elided = Preamble.of(
        runner_prompt="Blizzard prose.",
        workspace_prompt="prose",
        environments=_ENVS,
        lease_id="lease_2",
        runner_id="runner-local",
        chunk_id="ch_1",
        prior=fresh.fingerprint,
    )

    assert RESUME_STANDING_UNCHANGED in elided.text  # it really did elide
    assert elided.fingerprint == fresh.fingerprint
