import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { KitAsyncState, KitPanel, asyncState, type KitAsyncStateValue } from 'fleet';

import { FleetSpend } from './fleet-spend';
import { injectFleetSpendDays } from './fleet-spend-days';

/** The desktop rail's Fleet spend panel: {@link FleetSpend}'s Today and Yesterday rows, sized to its content. */
@Component({
  selector: 'app-fleet-spend-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetSpend, KitAsyncState, KitPanel],
  templateUrl: './fleet-spend-panel.html',
  styleUrl: './fleet-spend-panel.css',
})
export class FleetSpendPanel {
  protected readonly spend = injectFleetSpendDays();

  /** Never `'empty'`: the spend read always resolves to an aggregate. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.spend.today, false));
}
