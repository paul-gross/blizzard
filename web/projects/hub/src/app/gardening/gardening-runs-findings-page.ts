import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitAsyncState } from 'fleet';

/**
 * The `/gardening/runs-and-findings` sub-tab (blizzard#397) — this chunk builds only
 * the shell's empty state; every run's row and the findings it delivered are
 * `plans/garden/user-interface.md`'s "Reading what a run saw" and "Triage" sections,
 * each its own later issue.
 */
@Component({
  selector: 'app-gardening-runs-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './gardening-runs-findings-page.html',
})
export class GardeningRunsFindingsPage {}
