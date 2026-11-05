import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { compactRef, KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow } from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';

/** One row of the routine list — just enough to pick a routine. Selection keys on
 * `slug` (the routine's immutable handle), not `routineId`; `name` is what it reads as.
 * `routineId` renders as its own compact ref. */
export interface RoutineListRowVm {
  readonly routineId: string;
  readonly slug: string;
  readonly name: string;
  readonly graphName: string;
  /** Whether the routine's effective graph has no effective mint. */
  readonly blocked: boolean;
  /** Whether the routine's own retire/enable brake reads retired. */
  readonly retired: boolean;
}

/**
 * The gardening routine panel's routine list — presentational only, no query
 * injection. Renders the rows it is handed on `fleet-kit-select-row`, highlights
 * `selectedSlug`, and emits a `routinePick` event on a row click; the container
 * owns what "selected" then does.
 */
@Component({
  selector: 'app-routine-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow],
  templateUrl: './routine-list.html',
  styleUrl: './routine-list.css',
})
export class FleetRoutineList {
  readonly rows = input.required<readonly RoutineListRowVm[]>();
  readonly selectedSlug = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Named `routinePick`, not `select` — `@angular-eslint/no-output-native` forbids an
   * output shadowing the native DOM `select` event. */
  readonly routinePick = output<string>();

  protected readonly compactRef = compactRef;

  /** The lifecycle badge's tone, from the hub's one lifecycle owner. */
  protected readonly lifecycleTone = lifecycleTone;

  protected pick(slug: string): void {
    this.routinePick.emit(slug);
  }
}
