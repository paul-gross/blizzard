import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitSelectRow } from '../kit/kit-select-row';

/** One row of the routine list — just enough to pick a routine (D8 names the CLI verb
 * behind the read this list serves: `hub routine list`). Selection keys on `name`
 * (`hub/store/schema.py`'s `uq_routines_name`), not `routineId` — the route param
 * this list's selection drives (`app.routes.ts`'s `routine/:routineName`) names a
 * routine the same way. `routineId` renders as its own compact ref. */
export interface RoutineListRowVm {
  readonly routineId: string;
  readonly name: string;
  readonly graphName: string;
  /** Whether the routine's effective graph has no effective mint (D7) — the
   * container's own `blocked` resolution, generalized from the selected routine
   * alone to every row in the list. */
  readonly blocked: boolean;
}

/**
 * The gardening routine panel's routine list — presentational only, no query
 * injection. Renders the rows it is handed on `fleet-kit-select-row`, highlights
 * `selectedName`, and emits a `routinePick` event on a row click; the container
 * owns what "selected" then does.
 */
@Component({
  selector: 'fleet-routine-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitSelectRow],
  templateUrl: './routine-list.html',
  styleUrl: './routine-list.css',
})
export class FleetRoutineList {
  readonly rows = input.required<readonly RoutineListRowVm[]>();
  readonly selectedName = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Named `routinePick`, not `select` — `@angular-eslint/no-output-native` forbids an
   * output shadowing the native DOM `select` event. */
  readonly routinePick = output<string>();

  protected readonly compactRef = compactRef;

  protected pick(name: string): void {
    this.routinePick.emit(name);
  }
}
