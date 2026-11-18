import { ChangeDetectionStrategy, Component, signal } from '@angular/core';

import { KitButton } from './kit-button';
import type { KitChipOption } from './kit-chips';
import { KitGrid } from './kit-grid';
import { KitGridCell } from './kit-grid-cell';
import { KitGridRow } from './kit-grid-row';
import { KitGridWidget } from './kit-grid-widget';
import { KitSelect } from './kit-select';

/**
 * The grid bridge's fixture — the spec's two rows, each with text cells, a select and a button. The first row's
 * controls are enabled, the second's are disabled. Shared by the `Kit/Grid*` stories and the grid shell sweep,
 * so the sweep drives exactly what the stories show. Not part of the kit's public surface.
 */
@Component({
  selector: 'fleet-kit-grid-fixture',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitGrid, KitGridRow, KitGridCell, KitGridWidget, KitSelect, KitButton],
  templateUrl: './kit-grid.fixture.html',
})
export class KitGridFixture {
  protected readonly rows = [
    { name: 'ada', disabled: false },
    { name: 'grace', disabled: true },
  ];
  protected readonly options: readonly KitChipOption[] = [
    { value: 'guest', label: 'guest' },
    { value: 'contributor', label: 'contributor' },
    { value: 'admin', label: 'admin' },
  ];
  protected readonly picked = signal<Record<string, string>>({});
  /** Native clicks the Remove buttons produced — the activation count the sweep asserts. */
  readonly clicks = signal(0);
  /** The values chosen, in order — `name:value`. */
  readonly choices = signal<readonly string[]>([]);

  protected pick(name: string, value: string): void {
    this.picked.update((p) => ({ ...p, [name]: value }));
    this.choices.update((c) => [...c, `${name}:${value}`]);
  }
}
