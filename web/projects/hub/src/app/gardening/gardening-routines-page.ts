import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitAsyncState } from 'fleet';

/**
 * The `/gardening/routines` sub-tab (blizzard#397) — this chunk builds only the
 * shell's empty state; what a deployment has declared, its standing health, and the
 * act of running one by hand are `plans/garden/user-interface.md`'s "Declaring and
 * running a routine" section, each its own later issue.
 */
@Component({
  selector: 'app-gardening-routines-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './gardening-routines-page.html',
})
export class GardeningRoutinesPage {}
