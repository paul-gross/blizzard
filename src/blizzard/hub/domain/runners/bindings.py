"""A runner's bindings and subscription checked against its host's latest report.

Nothing here is stored: a host reports on every tick, so a flag kept from the runner's last registration would go
stale between the report and the next registration. A flagged binding is never refused — the runner keeps
registering — it is only offered no work."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.runners.host import Host
from blizzard.hub.domain.runners.registration import RunnerBinding, RunnerRegistration


@domain_model
@dataclass(frozen=True)
class BindingFlags:
    """What a host's report says against a runner's declaration: the workspace ids it has not reported, and whether
    the runner's subscription is outside its roster."""

    unreported_workspaces: frozenset[str] = frozenset()
    subscription_unrostered: bool = False

    def workspace_unreported(self, binding: RunnerBinding) -> bool:
        """Whether the host has not reported the binding's workspace."""
        return binding.workspace_id in self.unreported_workspaces

    def withholds(self, binding: RunnerBinding) -> bool:
        """Whether no work is offered through the binding: its workspace is unreported, or the subscription every
        binding spends is unrostered."""
        return self.subscription_unrostered or self.workspace_unreported(binding)


def flag_bindings(
    bindings: Sequence[RunnerBinding] | None, subscription: str | None, host: Host | None
) -> BindingFlags:
    """The flags ``host`` raises against a declaration. Nothing is flagged without a host, or while the host has never
    reported — there is no report to check against."""
    if host is None or host.never_connected():
        return BindingFlags()
    reported = {workspace.workspace_id for workspace in host.workspaces}
    return BindingFlags(
        unreported_workspaces=frozenset(b.workspace_id for b in bindings or () if b.workspace_id not in reported),
        subscription_unrostered=subscription is not None
        and subscription not in {declared.slug for declared in host.subscriptions},
    )


def offered_projects(
    registration: RunnerRegistration,
    host: Host | None,
    *,
    project: str | None = None,
    projects: Collection[str] | None = None,
) -> frozenset[str]:
    """The ids of the projects the matched peek may offer ``registration`` work from. None is offered under an
    unrostered subscription, with bindings or without. Otherwise a runner without bindings is offered every project it
    serves, and one with them is not offered a project reached only through withheld bindings. ``project`` keeps only
    the declaration entry it names, by the entry as declared or the id it resolved to; no such entry, or an unresolved
    one, leaves nothing. ``projects`` keeps the union of the entries it names, matched the same way, and offers nothing
    when empty. Neither widens what the runner can reach."""
    flags = flag_bindings(registration.bindings, registration.subscription, host)
    if flags.subscription_unrostered:
        return frozenset()
    reached = {entry.project_id for entry in registration.served if entry.project_id}
    if registration.bindings is not None:
        reachable = {b.project for b in registration.bindings if not flags.withholds(b)}
        reached = {entry.project_id for entry in registration.served if entry.project_id and entry.entry in reachable}
    if project is None and projects is None:
        return frozenset(reached)
    wanted = {project} if project is not None else set(projects or ())
    named = {
        entry.project_id
        for entry in registration.served
        if entry.project_id and wanted & {entry.entry, entry.project_id}
    }
    return frozenset(reached & named)


def claimable_projects(registration: RunnerRegistration, host: Host | None) -> frozenset[str]:
    """The ids of the projects ``registration`` can take a claim on now: none while a runner-level bar stands
    (:meth:`RunnerRegistration.claim_bar`), otherwise the projects the peek offers it. Liveness is not judged — the
    claim does not."""
    if registration.claim_bar() is not None:
        return frozenset()
    return offered_projects(registration, host)
