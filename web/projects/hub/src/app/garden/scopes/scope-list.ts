import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { FleetWhen, KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow } from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';

/** One row of the scope list — slug and retired state; a scope has no id at all, the
 * slug *is* the id. */
export interface ScopeRowVm {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
  /** When a run last swept the scope; `null` until the runs read answers. */
  readonly swept: ScopeSweptVm | null;
}

/** When a run in the runs window last swept a scope: `at` is that run's mint instant, `null` when the window holds no
 * sweep of it — then `notSwept` is what the scope shows instead. */
export interface ScopeSweptVm {
  readonly at: string | null;
  readonly notSwept: string;
}

/** Emitted when the operator sets a scope's description in place — `FleetScopePanel`'s
 * own event now that description editing lives there. */
export interface ScopeDescriptionEditEvent {
  readonly slug: string;
  readonly description: string;
}

/**
 * The gardening scope list — a selection list only: every scope's slug, its description and when a run last swept it,
 * retired ones marked as such, on `fleet-kit-select-row`. Presentational, no query injection.
 * Description editing and the retire/re-enable controls live in `FleetScopePanel`
 * now; this component only picks.
 */
@Component({
  selector: 'app-scope-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge, KitSelectRow],
  templateUrl: './scope-list.html',
  styleUrl: './scope-list.css',
})
export class FleetScopeList {
  readonly rows = input.required<readonly ScopeRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly selectedSlug = input<string | null>(null);

  readonly scopePick = output<string>();

  /** The lifecycle badge's tone, from the hub's one lifecycle owner. */
  protected readonly lifecycleTone = lifecycleTone;

  protected pick(slug: string): void {
    this.scopePick.emit(slug);
  }
}
