import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { harnessName, KitAsyncState, KitBadge, KitButton, KitErrorLine, type KitAsyncStateValue } from 'fleet';

import { type SelftestRow, selftestTone } from './selftest.model';

/** {@link LocalSelftest}'s presentational view: a Run control per harness, and its latest run's
 * status, checks and error once it reads back. */
@Component({
  selector: 'app-selftest-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitErrorLine],
  templateUrl: './selftest-view.html',
  styleUrl: './selftest-view.css',
})
export class SelftestView {
  protected readonly harnessName = harnessName;
  protected readonly tone = selftestTone;
  readonly rows = input<readonly SelftestRow[]>([]);
  readonly state = input.required<KitAsyncStateValue>();
  /** Emitted with the harness id to selftest. */
  readonly runSelftest = output<string>();
}
