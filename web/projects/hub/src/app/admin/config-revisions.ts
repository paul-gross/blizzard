import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { FleetWhen, KitAsyncState, type KitAsyncStateValue } from 'fleet';

import type { RevisionRowVm } from './config-history.model';

/**
 * A record's Revisions list — one row per change, newest first: the revision, what
 * changed, when, and who through which door. Presentational.
 */
@Component({
  selector: 'app-config-revisions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState],
  templateUrl: './config-revisions.html',
  styleUrl: './config-revisions.css',
})
export class ConfigRevisions {
  readonly rows = input.required<readonly RevisionRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly testidPrefix = input.required<string>();
}
