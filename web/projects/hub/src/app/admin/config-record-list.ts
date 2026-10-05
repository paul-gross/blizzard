import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow, type Tone } from 'fleet';

/** A marker a config row or detail header carries. */
export interface ConfigBadgeVm {
  readonly label: string;
  readonly tone: Tone;
}

/** One row of a config list — a record, or a change log entry. */
export interface ConfigRowVm {
  /** The selection key the row's detail route carries. */
  readonly key: string;
  readonly title: string;
  /** Short secondary facts, rendered in order under the title. */
  readonly sub: readonly string[];
  readonly badges: readonly ConfigBadgeVm[];
  /** The record's revision, or `null` for a row with none to show. */
  readonly revision: number | null;
  readonly retired: boolean;
}

/**
 * A config surface's selection list — one select row per record or change entry,
 * with its badges and revision. Presentational: rows in, picks out.
 */
@Component({
  selector: 'app-config-record-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow],
  templateUrl: './config-record-list.html',
  styleUrl: './config-record-list.css',
})
export class ConfigRecordList {
  readonly rows = input.required<readonly ConfigRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly emptyText = input.required<string>();
  readonly selectedKey = input<string | null>(null);
  /** Roots every testid this list renders. */
  readonly testidPrefix = input.required<string>();

  readonly pick = output<string>();
}
