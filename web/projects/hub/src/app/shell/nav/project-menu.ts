import {
  ChangeDetectionStrategy,
  Component,
  input,
  output,
} from '@angular/core';
import { KitMenuItemRadio, KitMenuPanel } from 'fleet';

import type { LensChoice } from './project-lens.model';

/**
 * The mobile titlebar's project lens submenu — **All**, then one radio item per project of the tenant, the lens's own
 * choice checked. Presentational, as the desktop lens control is: it takes the `choices` and the `current` slug, and
 * emits the chosen slug on `choose`.
 */
@Component({
  selector: 'app-project-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenuItemRadio, KitMenuPanel],
  templateUrl: './project-menu.html',
})
export class ProjectMenu {
  /** All, then the tenant's projects by name. */
  readonly choices = input.required<readonly LensChoice[]>();

  /** The slug the lens is on, `null` for All. */
  readonly current = input.required<string | null>();

  /** The slug of the chosen choice, `null` for All. */
  readonly choose = output<string | null>();

  protected onChoose(choice: LensChoice): void {
    if (choice.slug !== this.current()) this.choose.emit(choice.slug);
  }
}
