"""``[runner.throttle]``: what the host parses into :class:`ThrottleSettings`, which mistakes halt that runner alone and
name their key, and that rendering a runner back out keeps its levers."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from blizzard.runner.host_config import BuiltRunner, HaltedRunner, throttle_toml
from blizzard.runner.throttle.settings import (
    BudgetSettings,
    HoldOrRelease,
    PaceSettings,
    ScheduleSettings,
    Span,
    TaperStep,
    ThrottleSettings,
    UtilizationSettings,
)
from tests.config_inputs import load_host_config
from tests.host_files import write_minimal_host

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}
_SUBSCRIPTION = '[[subscription]]\nslug = "plan"\nname = "Plan"\nprovider = "{provider}"\n'

_EVERY_LEVER = """
subscription = "plan"
[runner.throttle.pace]
window = "7d"
lead_pct = 3
[[runner.throttle.pace.taper]]
from_pct = 50
lead_pct = 2
[[runner.throttle.pace.taper]]
from_pct = 90
lead_pct = 1
[runner.throttle.utilization]
below_pct = 95
windows = ["5h", "7d"]
[runner.throttle.budget]
usd = 40
window_hours = 12
[runner.throttle.schedule]
days = ["mon", "fri"]
hours = ["19:00-08:00", "12:30-13:00"]
"""

_SETTINGS = ThrottleSettings(
    pace=PaceSettings("7d", 3.0, (TaperStep(50.0, 2.0), TaperStep(90.0, 1.0))),
    utilization=UtilizationSettings(95.0, ("5h", "7d")),
    budget=BudgetSettings(40.0, 12.0),
    schedule=ScheduleSettings((0, 4), (Span(19 * 60, 8 * 60), Span(12 * 60 + 30, 13 * 60))),
)


_CHOICES = '[runner.throttle]\non_decline = "release"\non_usage_limit = "release"\n'
_SETTINGS_RELEASING = dataclasses.replace(
    _SETTINGS, on_decline=HoldOrRelease.RELEASE, on_usage_limit=HoldOrRelease.RELEASE
)


def _projected(root: Path, runner: str, *, provider: str = "anthropic") -> BuiltRunner | HaltedRunner:
    write_minimal_host(root, top=_SUBSCRIPTION.format(provider=provider), runner=runner)
    (projection,) = load_host_config(root).projections(_TOKEN)
    return projection


def _halted(root: Path, runner: str, *, provider: str = "anthropic") -> str:
    projection = _projected(root, runner, provider=provider)
    assert isinstance(projection, HaltedRunner)
    return "\n".join(projection.errors)


def test_a_runner_with_no_throttle_table_configures_none(tmp_path: Path) -> None:
    built = _projected(tmp_path, 'subscription = "plan"')
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle is None


def test_every_lever_parses_to_its_settings(tmp_path: Path) -> None:
    built = _projected(tmp_path, _EVERY_LEVER)
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == _SETTINGS


def test_the_defaults_fill_what_a_lever_leaves_out(tmp_path: Path) -> None:
    built = _projected(
        tmp_path,
        'subscription = "plan"\n[runner.throttle.pace]\n[runner.throttle.utilization]\nbelow_pct = 100\n'
        "[runner.throttle.schedule]\ndays = ['sat']\n",
    )
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == ThrottleSettings(
        PaceSettings(), UtilizationSettings(100.0), schedule=ScheduleSettings((5,), ())
    )


def test_rendering_a_runner_and_loading_it_keeps_its_levers(tmp_path: Path) -> None:
    write_minimal_host(tmp_path / "a", top=_SUBSCRIPTION.format(provider="anthropic"), runner=_EVERY_LEVER)
    rendered = load_host_config(tmp_path / "a").to_toml()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "blizzard-host.toml").write_text(rendered)
    (built,) = load_host_config(tmp_path / "b").projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == _SETTINGS


@pytest.mark.parametrize("lead", [-100, -5, 0, 100])
def test_signed_leads_round_trip(tmp_path: Path, lead: int) -> None:
    runner = f'subscription = "plan"\n[runner.throttle.pace]\nlead_pct = {lead}\n'
    runner += f"[[runner.throttle.pace.taper]]\nfrom_pct = 50\nlead_pct = {lead}\n"
    write_minimal_host(tmp_path / "a", top=_SUBSCRIPTION.format(provider="anthropic"), runner=runner)
    rendered = load_host_config(tmp_path / "a").to_toml()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "blizzard-host.toml").write_text(rendered)
    (built,) = load_host_config(tmp_path / "b").projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == ThrottleSettings(pace=PaceSettings(None, lead, (TaperStep(50, lead),)))


@pytest.mark.parametrize(
    ("runner", "key"),
    [
        ("[runner.throttle.pace]\nlead_pct = 101", "throttle.pace.lead_pct"),
        ("[runner.throttle.pace]\nlead_pct = -101", "throttle.pace.lead_pct"),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 1\nlead_pct = -101",
            "throttle.pace.taper[1].lead_pct",
        ),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 1\nlead_pct = 101",
            "throttle.pace.taper[1].lead_pct",
        ),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = -1\nlead_pct = -1",
            "throttle.pace.taper[1].from_pct",
        ),
        ("[runner.throttle.utilization]\nbelow_pct = -1", "throttle.utilization.below_pct"),
        ("[runner.throttle.pace]\nwindow = ''", "throttle.pace.window"),
        ("[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nlead_pct = 1", "throttle.pace.taper[1].from_pct"),
        ("[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 1", "throttle.pace.taper[1].lead_pct"),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 80\nlead_pct = 1\n"
            "[[runner.throttle.pace.taper]]\nfrom_pct = 120\nlead_pct = 1",
            "throttle.pace.taper[2].from_pct",
        ),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 80\nlead_pct = 1\n"
            "[[runner.throttle.pace.taper]]\nfrom_pct = 80\nlead_pct = 1",
            "throttle.pace.taper[2].from_pct",
        ),
        (
            "[runner.throttle.pace]\n[[runner.throttle.pace.taper]]\nfrom_pct = 80\nlead_pct = 1\n"
            "[[runner.throttle.pace.taper]]\nfrom_pct = 50\nlead_pct = 1",
            "throttle.pace.taper[2].from_pct",
        ),
        ("[runner.throttle.utilization]", "throttle.utilization.below_pct"),
        ("[runner.throttle.utilization]\nbelow_pct = 0", "throttle.utilization.below_pct"),
        ("[runner.throttle.utilization]\nbelow_pct = 101", "throttle.utilization.below_pct"),
        ("[runner.throttle.utilization]\nbelow_pct = 90\nwindows = []", "throttle.utilization.windows"),
        ("[runner.throttle.budget]", "throttle.budget.usd"),
        ("[runner.throttle.budget]\nusd = 0", "throttle.budget.usd"),
        ("[runner.throttle.budget]\nusd = -5", "throttle.budget.usd"),
        ("[runner.throttle.budget]\nusd = 5\nwindow_hours = 0", "throttle.budget.window_hours"),
        ("[runner.throttle.schedule]", "throttle.schedule"),
        ("[runner.throttle.schedule]\ndays = ['funday']", "throttle.schedule.days"),
        ("[runner.throttle.schedule]\ndays = []", "throttle.schedule.days"),
        ("[runner.throttle.schedule]\nhours = ['25:00-26:00']", "throttle.schedule.hours"),
        ("[runner.throttle.schedule]\nhours = ['7am-9am']", "throttle.schedule.hours"),
        ("[runner.throttle.schedule]\nhours = ['08:00-08:00']", "throttle.schedule.hours"),
        ("[runner.throttle]\non_decline = 'drop'", "throttle.on_decline"),
        ("[runner.throttle]\non_decline = true", "throttle.on_decline"),
        ("[runner.throttle]\non_usage_limit = 'RELEASE'", "throttle.on_usage_limit"),
    ],
)
def test_a_mistake_halts_the_runner_and_names_its_key(tmp_path: Path, runner: str, key: str) -> None:
    assert key in _halted(tmp_path, f'subscription = "plan"\n{runner}')


def test_a_mistake_halts_that_runner_alone(tmp_path: Path) -> None:
    write_minimal_host(
        tmp_path,
        top='host_token = "host"\n' + _SUBSCRIPTION.format(provider="anthropic"),
        runner=(
            'subscription = "plan"\n[runner.throttle.utilization]\nbelow_pct = 0\n\n'
            '[[runner]]\nid = "rn_other"\nname = "other"\nprojects = []\n'
        ),
    )
    halted, other = load_host_config(tmp_path).projections({**_TOKEN, "BZ_HUB_TOKEN_OTHER": "tok"})
    assert isinstance(halted, HaltedRunner)
    assert isinstance(other, BuiltRunner)


def test_a_sampled_lever_needs_a_subscription(tmp_path: Path) -> None:
    halted = _halted(tmp_path, "[runner.throttle.pace]")
    assert "throttle.pace" in halted
    assert "names no subscription" in halted


def test_a_sampled_lever_needs_a_provider_that_is_sampled(tmp_path: Path) -> None:
    halted = _halted(
        tmp_path, 'subscription = "plan"\n[runner.throttle.utilization]\nbelow_pct = 90', provider="custom"
    )
    assert "throttle.utilization" in halted
    assert "never sampled" in halted


def test_a_schedule_needs_no_subscription(tmp_path: Path) -> None:
    built = _projected(tmp_path, "[runner.throttle.schedule]\ndays = ['mon']")
    assert isinstance(built, BuiltRunner)


def test_a_budget_defaults_to_a_day_and_needs_no_subscription(tmp_path: Path) -> None:
    built = _projected(tmp_path, "[runner.throttle.budget]\nusd = 5")
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == ThrottleSettings(budget=BudgetSettings(5.0, 24.0))


@pytest.mark.parametrize(
    ("cost", "budget", "refused"),
    [
        ("runner_ceiling_usd = 50", "usd = 50", True),
        ("runner_ceiling_usd = 50", "usd = 60", True),
        ("runner_ceiling_usd = 50", "usd = 49.99", False),
        # The ceiling's 50 over 24h is 25 over 12h: a 12h budget of 25 meets it, of 24 stays under it.
        ("runner_ceiling_usd = 50", "usd = 25\nwindow_hours = 12", True),
        ("runner_ceiling_usd = 50", "usd = 24\nwindow_hours = 12", False),
        ("runner_ceiling_usd = 50\nwindow_hours = 48", "usd = 24", False),
        ("runner_ceiling_usd = 50\nwindow_hours = 48", "usd = 25", True),
    ],
)
def test_a_budget_at_or_above_the_ceiling_over_the_same_hours_halts_the_runner(
    tmp_path: Path, cost: str, budget: str, refused: bool
) -> None:
    projection = _projected(tmp_path, f"[runner.cost]\n{cost}\n[runner.throttle.budget]\n{budget}")
    assert isinstance(projection, HaltedRunner) is refused
    if refused:
        assert "throttle.budget.usd" in "\n".join(projection.errors)  # type: ignore[union-attr]


def test_neither_choice_set_holds_on_both_and_configures_nothing(tmp_path: Path) -> None:
    built = _projected(tmp_path, 'subscription = "plan"\n[runner.throttle]\non_decline = "hold"')
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle is None


def test_each_choice_parses_beside_the_levers(tmp_path: Path) -> None:
    built = _projected(tmp_path, _EVERY_LEVER.replace("[runner.throttle.pace]", _CHOICES + "[runner.throttle.pace]"))
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == _SETTINGS_RELEASING


def test_a_choice_alone_configures_the_table(tmp_path: Path) -> None:
    built = _projected(tmp_path, '[runner.throttle]\non_usage_limit = "release"')
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE)


def test_a_release_on_decline_alone_round_trips_through_its_table() -> None:
    settings = ThrottleSettings(on_decline=HoldOrRelease.RELEASE)
    assert throttle_toml(settings) == '\n[runner.throttle]\non_decline = "release"\n'


def test_rendering_a_runner_and_loading_it_keeps_its_choices(tmp_path: Path) -> None:
    runner = _EVERY_LEVER.replace("[runner.throttle.pace]", _CHOICES + "[runner.throttle.pace]")
    write_minimal_host(tmp_path / "a", top=_SUBSCRIPTION.format(provider="anthropic"), runner=runner)
    rendered = load_host_config(tmp_path / "a").to_toml()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "blizzard-host.toml").write_text(rendered)
    (built,) = load_host_config(tmp_path / "b").projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == _SETTINGS_RELEASING


def test_a_choice_alone_survives_rendering(tmp_path: Path) -> None:
    write_minimal_host(tmp_path / "a", top="", runner='[runner.throttle]\non_decline = "release"')
    rendered = load_host_config(tmp_path / "a").to_toml()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "blizzard-host.toml").write_text(rendered)
    (built,) = load_host_config(tmp_path / "b").projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert built.config.throttle == ThrottleSettings(on_decline=HoldOrRelease.RELEASE)
