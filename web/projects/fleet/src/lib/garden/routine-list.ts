import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';

/** One row of the routine list — just enough to pick a routine (D8 names the CLI verb
 * behind the read this list serves: `hub routine list`). */
export interface RoutineListRowVm {
  readonly routineId: string;
  readonly name: string;
  readonly graphName: string;
}

/**
 * The gardening routine panel's routine list — presentational only, no query
 * injection. Renders the rows it is handed, highlights `selectedId`, and emits a
 * `select` event on a row click; the container owns what "selected" then does.
 */
@Component({
  selector: 'fleet-routine-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './routine-list.html',
  styleUrl: './routine-list.css',
})
export class FleetRoutineList {
  readonly rows = input.required<readonly RoutineListRowVm[]>();
  readonly selectedId = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Named `routinePick`, not `select` — `@angular-eslint/no-output-native` forbids an
   * output shadowing the native DOM `select` event. */
  readonly routinePick = output<string>();

  protected pick(routineId: string): void {
    this.routinePick.emit(routineId);
  }
}
