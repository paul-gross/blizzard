import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { KitPanel } from 'fleet';

import { PanelFilters } from '../core/panel-filters';
import { injectPanelRunners, injectRunnerProjects } from '../core/panel-reads';
import type { RunnerOwner } from '../core/panel-reads.model';
import { servesProject } from '../core/panel-reads.model';
import { HostEnvironments } from '../host/host-environments';
import { HostRegistration } from '../host/host-registration';
import { HostRunners } from '../host/host-runners';
import { HostSubscriptions } from '../host/host-subscriptions';
import { RunnerMachine } from './runner-machine';

/**
 * The `machine` route — the host and its runners' own machines. Across the top, what
 * belongs to the host: its registration at its hub, the runners it serves or holds
 * halted, its environments grouped by workspace, and its subscriptions with the runners
 * spending them. Below, one {@link RunnerMachine} per serving runner the filters leave —
 * the one the Runner filter names, those serving the Project filter's project. Each panel
 * is a self-fetching container, so this page owns nothing beyond choosing the runners and
 * laying them out.
 */
@Component({
  selector: 'app-machine-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostEnvironments, HostRegistration, HostRunners, HostSubscriptions, KitPanel, RunnerMachine],
  templateUrl: './machine-page.html',
  styleUrl: './machine-page.css',
})
export class MachinePage {
  private readonly filters = inject(PanelFilters);
  private readonly runners = injectPanelRunners();
  private readonly projects = injectRunnerProjects();

  /** The serving runners the filters leave, each given its own section. */
  protected readonly sections = computed<readonly RunnerOwner[]>(() => {
    const project = this.filters.project();
    const byRunner = this.projects();
    return this.runners.scoped().filter((owner) => servesProject(byRunner.get(owner.runnerId) ?? [], project));
  });
}
