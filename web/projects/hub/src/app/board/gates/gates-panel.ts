import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { type KitAsyncStateValue, asyncState } from 'fleet';
import { GatesPanelView } from './gates-view';
import { injectHubDecisionsQuery } from './gates.query';

/**
 * The open-gates panel — every decision awaiting a person across the fleet, in
 * the right rail, so an operator finds each gate without opening cards. Clicking
 * a gate opens its chunk, where it is resolved.
 *
 * A container: it owns the fleet-wide decisions query and renders the
 * presentational {@link GatesPanelView}. The live-update service re-reads it on
 * `decision-opened` / `decision-resolved`.
 */
@Component({
  selector: 'app-gates-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GatesPanelView],
  templateUrl: './gates-panel.html',
})
export class GatesPanel {
  private readonly query = injectHubDecisionsQuery();

  /** Emitted with a chunk id when a gate is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  /** Every open gate across the fleet; empty until the first read resolves. */
  protected readonly gates = computed(() => this.query.data() ?? []);

  /** The decisions query's async state. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.query, this.gates().length === 0));
}
