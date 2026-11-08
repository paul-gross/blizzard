"""A runner's bindings and subscription checked against its host's latest report.

Nothing here is stored: every flag is derived from the host's latest report. A flagged binding is never refused — the
runner keeps registering — it is only offered no work."""

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


@domain_model
@dataclass(frozen=True)
class WorkEligibility:
    """What a runner's declaration and its host's latest report admit — the one definition both the matched peek (which
    projects are offered) and the direct claim (whether this project may be claimed through this workspace) read.

    Nothing is admitted under an unrostered subscription, with bindings or without. A runner with no bindings (legacy)
    is admitted by its served declaration alone, and its workspace is not judged. One with an empty list is admitted
    nothing. Otherwise a project is admitted only through a binding that is not withheld and names a served entry
    resolving to it."""

    registration: RunnerRegistration
    host: Host | None

    @property
    def flags(self) -> BindingFlags:
        return flag_bindings(self.registration.bindings, self.registration.subscription, self.host)

    def offered_projects(self, *, projects: Collection[str] | None = None) -> frozenset[str]:
        """The ids of the projects the matched peek may offer. ``projects`` keeps the union of the declaration entries
        it names, each by the entry as declared or the id it resolved to, and offers nothing when empty. It never
        widens what the runner can reach."""
        registration = self.registration
        flags = self.flags
        if flags.subscription_unrostered:
            return frozenset()
        reached = {entry.project_id for entry in registration.served if entry.project_id}
        if registration.bindings is not None:
            reachable = {b.project for b in registration.bindings if not flags.withholds(b)}
            reached = {
                entry.project_id for entry in registration.served if entry.project_id and entry.entry in reachable
            }
        if projects is None:
            return frozenset(reached)
        wanted = set(projects)
        named = {
            entry.project_id
            for entry in registration.served
            if entry.project_id and wanted & {entry.entry, entry.project_id}
        }
        return frozenset(reached & named)

    def admits_claim(self, project_id: str, workspace_id: str) -> bool:
        """Whether a claim on a chunk of ``project_id`` through ``workspace_id`` is admitted."""
        registration = self.registration
        flags = self.flags
        if flags.subscription_unrostered:
            return False
        if registration.bindings is None:
            return True
        admitting = {
            b.project for b in registration.bindings if b.workspace_id == workspace_id and not flags.withholds(b)
        }
        return any(entry.project_id == project_id and entry.entry in admitting for entry in registration.served)


def offered_projects(
    registration: RunnerRegistration,
    host: Host | None,
    *,
    projects: Collection[str] | None = None,
) -> frozenset[str]:
    """The ids of the projects the matched peek may offer ``registration`` work from
    (:meth:`WorkEligibility.offered_projects`)."""
    return WorkEligibility(registration, host).offered_projects(projects=projects)


def claimable_projects(registration: RunnerRegistration, host: Host | None) -> frozenset[str]:
    """The ids of the projects ``registration`` can take a claim on now: none while a runner-level bar stands
    (:meth:`RunnerRegistration.claim_bar`), otherwise the projects the peek offers it. Liveness is not judged — the
    claim does not."""
    if registration.claim_bar() is not None:
        return frozenset()
    return offered_projects(registration, host)
