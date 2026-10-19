import { ChangeDetectionStrategy, Component, type TemplateRef, computed, input, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FleetWhen, KitAsyncState, KitBadge, KitBeacon, type KitFact, KitFactList, KitSwatch } from 'fleet';

import type { HostRunnerRow } from './host-runner-row.model';

/**
 * One runner as a wide row on the host page, as the hub's Fleet page draws one: a header
 * line (its light, name, short ref, harnesses and agents, last tick, and its verbs,
 * projected in), then a fact grid — what it binds, what it spends, its harnesses, its hub
 * link, gates and state — beside the chunks it holds, each opening on the board.
 *
 * Presentational only: the page folds {@link HostRunnerRow} and projects the pause control.
 */
@Component({
  selector: 'app-host-runner-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge, KitBeacon, KitFactList, KitSwatch, RouterLink],
  templateUrl: './host-runner-row.html',
  styleUrl: './host-runner-row.css',
})
export class HostRunnerRowView {
  readonly row = input.required<HostRunnerRow>();

  private readonly binds = viewChild.required<TemplateRef<unknown>>('binds');
  private readonly spends = viewChild.required<TemplateRef<unknown>>('spends');
  private readonly harness = viewChild.required<TemplateRef<unknown>>('harness');
  private readonly hub = viewChild.required<TemplateRef<unknown>>('hub');
  private readonly gates = viewChild.required<TemplateRef<unknown>>('gates');
  private readonly problems = viewChild.required<TemplateRef<unknown>>('problems');
  private readonly state = viewChild.required<TemplateRef<unknown>>('state');

  /** A halted runner reports nothing but why it is halted, so it shows only its state. */
  protected readonly facts = computed<readonly KitFact[]>(() => {
    const row = this.row();
    if (row.halted) return [{ label: 'State', template: this.state() }];
    return [
      { label: 'Binds', template: this.binds(), testid: 'host-runner-binds' },
      ...(row.spends.length > 0 ? [{ label: 'Spends', template: this.spends(), testid: 'host-runner-spends' }] : []),
      { label: 'Harness', template: this.harness(), testid: 'host-runner-harness' },
      ...(row.hub ? [{ label: 'Hub', template: this.hub(), testid: 'host-runner-hub' }] : []),
      { label: 'Gates', template: this.gates(), testid: 'host-runner-gates' },
      ...(row.problems.length > 0 ? [{ label: 'Problems', template: this.problems(), testid: 'host-runner-problems' }] : []),
      { label: 'State', template: this.state(), testid: 'host-runner-state' },
    ];
  });
}
