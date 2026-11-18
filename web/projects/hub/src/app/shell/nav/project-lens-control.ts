import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitChip, KitChipGroup, KitChipOptionDirective, KitSwatch } from 'fleet/shell';

import type { LensChoice } from './project-lens.model';

/**
 * The project lens control in the view-tab row: **All**, then one chip per project of the tenant, the lens's own
 * choice selected. Presentational — the shell hands it the `choices` and the `current` slug, and hears the chosen slug
 * on `choose`; re-navigating under the lens is the shell's.
 *
 * The chips scroll inside the control rather than widen the row, so a tenant with many projects never pushes the tab
 * row past the viewport. The tenant is not here: it changes only from the profile menu.
 */
@Component({
  selector: 'app-project-lens-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip, KitChipGroup, KitChipOptionDirective, KitChipGroup, KitChipOptionDirective, KitSwatch],
  templateUrl: './project-lens-control.html',
  styleUrl: './project-lens-control.css',
})
export class ProjectLensControl {
  /** All, then the tenant's projects by name. */
  readonly choices = input.required<readonly LensChoice[]>();

  /** The slug the lens is on, `null` for All. */
  readonly current = input.required<string | null>();

  /** The slug of the chosen choice, `null` for All. */
  readonly choose = output<string | null>();

  protected hasCurrent(): boolean {
    return this.choices().some((choice) => choice.slug === this.current());
  }

  protected onChoose(value: unknown): void {
    const choice = this.choices().find((item) => item.slug === value);
    if (choice && choice.slug !== this.current()) this.choose.emit(choice.slug);
  }
}
