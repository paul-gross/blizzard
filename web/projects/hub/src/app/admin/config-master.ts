import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitButton, KitChips, type KitAsyncStateValue, KitMasterDetail } from 'fleet';

import { LIFECYCLE_FILTER_OPTIONS } from './config-filter.model';
import { ConfigRecordList, type ConfigRowVm } from './config-record-list';

/**
 * The frame every config surface shares: a `fleet-kit-master-detail` split with the
 * list on the left — its Active/Retired/All chips, its rows, and an optional "older"
 * pager — and the routed detail projected on the right. On a phone the split drills
 * down: the list alone until a row is picked, then the detail alone behind Back.
 * Presentational: the containers own the reads, the URL, and the selection.
 */
@Component({
  selector: 'app-config-master',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigRecordList, KitButton, KitChips, KitMasterDetail],
  templateUrl: './config-master.html',
  styleUrl: './config-master.css',
})
export class ConfigMaster {
  readonly paneId = input.required<string>();
  readonly listCaption = input.required<string>();
  /** The lifecycle filter shown, or `null` for a list with no filter. */
  readonly filter = input<string | null>(null);
  readonly rows = input.required<readonly ConfigRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly emptyText = input.required<string>();
  readonly selectedKey = input<string | null>(null);
  /** Whether the phone's one-pane drill-down is on. */
  readonly drilldown = input(false);
  readonly backLabel = input.required<string>();
  readonly testidPrefix = input.required<string>();
  /** A standing note under the list, or `null` for none. */
  readonly footer = input<string | null>(null);
  /** Whether an older page exists to load. */
  readonly hasOlder = input(false);
  readonly olderPending = input(false);

  readonly filterChange = output<string>();
  readonly pick = output<string>();
  readonly back = output<void>();
  readonly older = output<void>();

  protected readonly filterOptions = LIFECYCLE_FILTER_OPTIONS;
}
