import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { FleetWhen, KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow, type Tone } from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';

/** One state's errand count on a cadence row. */
export interface CadenceCountVm {
  readonly state: string;
  readonly label: string;
  readonly tone: Tone;
  readonly count: number;
}

/** One row of the cadence list. */
export interface CadenceRowVm {
  readonly slug: string;
  readonly name: string;
  readonly retired: boolean;
  /** The schedule and spacing as one line. */
  readonly rhythm: string;
  /** The next moment the schedule fires, `null` with no schedule or when retired. */
  readonly nextMoment: string | null;
  readonly choreCount: number;
  /** The errands counted by state, the states holding none left out. */
  readonly counts: readonly CadenceCountVm[];
  /** How many things stop the cadence's errands. */
  readonly attentionCount: number;
}

/**
 * The gardening cadence list — a selection list: each cadence's name, rhythm and next moment, its chore count, its
 * errands counted by state, and a badge when attention stops any of them. Presentational, no query injection.
 */
@Component({
  selector: 'app-cadence-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge, KitSelectRow],
  templateUrl: './cadence-list.html',
  styleUrl: './cadence-list.css',
})
export class FleetCadenceList {
  readonly rows = input.required<readonly CadenceRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly selectedSlug = input<string | null>(null);

  readonly cadencePick = output<string>();

  protected readonly lifecycleTone = lifecycleTone;

  protected pick(slug: string): void {
    this.cadencePick.emit(slug);
  }
}
