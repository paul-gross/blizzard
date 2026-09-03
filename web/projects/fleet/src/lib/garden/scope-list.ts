import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitSelectRow } from '../kit/kit-select-row';

/** One row of the scope list — slug and retired state; a scope has no id at all, the
 * slug *is* the id (`foundation/ids.py`). */
export interface ScopeRowVm {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
}

/** Emitted when the operator sets a scope's description in place — `FleetScopePanel`'s
 * own event now that description editing lives there. */
export interface ScopeDescriptionEditEvent {
  readonly slug: string;
  readonly description: string;
}

/**
 * The gardening scope list — a selection list only: every scope's slug, retired ones
 * marked as such, on `fleet-kit-select-row`. Presentational, no query injection.
 * Description editing and the retire/re-enable controls live in `FleetScopePanel`
 * now; this component only picks.
 */
@Component({
  selector: 'fleet-scope-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitSelectRow],
  templateUrl: './scope-list.html',
  styleUrl: './scope-list.css',
})
export class FleetScopeList {
  readonly rows = input.required<readonly ScopeRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly selectedSlug = input<string | null>(null);

  readonly scopePick = output<string>();

  protected pick(slug: string): void {
    this.scopePick.emit(slug);
  }
}
