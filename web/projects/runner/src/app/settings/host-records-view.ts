import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitPanel } from 'fleet';

import type { HostRecordKind } from './host-records.domain';
import type { HostRecordVm } from './host-records.model';

/** One kind's records — Workspaces or Subscriptions — each with its state badge, the open one's detail projected
 * beneath it, and Create for the superuser. Presentational: its container owns the read and the verbs. */
@Component({
  selector: 'app-host-records-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitPanel],
  templateUrl: './host-records-view.html',
  styleUrl: './host-records-view.css',
})
export class HostRecordsView {
  readonly kind = input.required<HostRecordKind>();
  readonly title = input.required<string>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly records = input.required<readonly HostRecordVm[]>();
  /** The record whose detail is open, `null` while none is. */
  readonly openKey = input<string | null>(null);
  /** Whether the session may create a record, so Create shows. */
  readonly canCreate = input(false);
  /** Holds Create off while a create is in flight. */
  readonly createDisabled = input(false);

  readonly create = output<void>();
  /** The record whose row was pressed: its detail opens, or closes when already open. */
  readonly openToggled = output<string>();
}
