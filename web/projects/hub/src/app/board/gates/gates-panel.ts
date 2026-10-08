import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { type KitAsyncStateValue, asyncState } from 'fleet';
import { GatesPanelView } from './gates-view';
import { injectHubDecisionsQuery } from './gates.query';

/**
 * The open-gates panel — every decision awaiting a person across the fleet.
 *
 * A container: it owns the fleet-wide decisions query and renders the
 * presentational {@link GatesPanelView}. Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
@Component({
  selector: 'app-gates-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GatesPanelView],
  templateUrl: './gates-panel.html',
})
export class GatesPanel {
  private readonly query = injectHubDecisionsQuery();

  /** Emitted with the gate's chunk id when a gate is activated. */
  readonly selectChunk = output<string>();

  /** Every open gate across the fleet; empty until the first read resolves. */
  protected readonly gates = computed(() => this.query.data() ?? []);

  /** The decisions query's async state. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.query, this.gates().length === 0));
}
