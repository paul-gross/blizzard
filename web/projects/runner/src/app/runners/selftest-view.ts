import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { harnessName, KitAsyncState, KitBadge, KitButton, KitErrorLine, type KitAsyncStateValue } from 'fleet';

import { checkTone, type SelftestRow, selftestPhase, selftestTone } from './selftest.model';

/** {@link LocalSelftest}'s presentational view: a Run control per harness, and its latest run's
 * status, its checks once settled, and its error. */
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
  protected readonly phase = selftestPhase;
  protected readonly checkTone = checkTone;
  readonly rows = input<readonly SelftestRow[]>([]);
  readonly state = input.required<KitAsyncStateValue>();
  /** Emitted with the harness id to selftest. */
  readonly runSelftest = output<string>();
}
