import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitAsyncState } from 'fleet';

/**
 * The `/gardening/proposals` sub-tab (blizzard#397) — this chunk builds only the
 * shell's empty state; the docket itself (`plans/garden/user-interface.md`'s "The
 * docket" section — reading a case and its evidence, passing, accepting) is its own
 * later issue. The sub-tab's live waiting count is shell chrome and lives on the
 * gardening tab's own strip instead — see `gardening-page.ts`.
 */
@Component({
  selector: 'app-gardening-proposals-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './gardening-proposals-page.html',
})
export class GardeningProposalsPage {}
