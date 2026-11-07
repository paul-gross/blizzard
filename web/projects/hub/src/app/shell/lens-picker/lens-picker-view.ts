import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink, type UrlTree } from '@angular/router';

import { KitAsyncState, type KitAsyncStateValue, KitBackBar } from 'fleet';
import { KitSwatch } from 'fleet/shell';

import type { LensChoice } from '../nav/project-lens.model';

/**
 * The phone lens picker's screen: a back row, the All row, then the tenant's projects — the lens's own choice
 * selected. The back and All rows need no read, so they show in every state; `state` governs only the project rows.
 */
@Component({
  selector: 'app-lens-picker-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBackBar, KitSwatch, RouterLink],
  templateUrl: './lens-picker-view.html',
  styleUrl: './lens-picker-view.css',
})
export class LensPickerView {
  /** The read of the tenant's projects: loading, failed, none, or loaded. */
  readonly state = input.required<KitAsyncStateValue>();

  /** All, then each project. */
  readonly choices = input.required<readonly LensChoice[]>();

  /** The slug the lens is on, `null` for All. */
  readonly current = input<string | null>(null);

  /** Where Back goes. */
  readonly backLink = input.required<UrlTree | string>();

  /** The slug of the choice made, `null` for All. */
  readonly choose = output<string | null>();

  protected readonly all = computed(() => this.choices().find((choice) => choice.slug === null) ?? null);
  protected readonly projects = computed(() => this.choices().filter((choice) => choice.slug !== null));
}
