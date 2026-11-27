import { Directive, inject, input, type OnInit } from '@angular/core';
import { Grid } from '@angular/aria/grid';

/**
 * An interactive table — put on the `<table>` whose body rows are {@link KitGridRow}s. Carries
 * Aria's `ngGrid`: the whole table is one Tab stop, the arrow keys move between cells, and a cell's
 * native control (wrapped by {@link KitGridWidget}) works with Enter or Space.
 *
 * Every grid binds `[softDisabled]="false"`: Aria defaults it to `true`, under which a disabled control in
 * the active cell leaves the table with no Tab stop. A host directive's input cannot be defaulted by the kit,
 * so the consumer's template carries the binding; a dev build throws when it is missing. `ariaLabel` names the grid.
 */
@Directive({
  selector: '[fleetKitGrid]',
  hostDirectives: [{ directive: Grid, inputs: ['disabled', 'softDisabled', 'rowWrap', 'colWrap'] }],
  host: { '[attr.aria-label]': 'ariaLabel()' },
})
export class KitGrid implements OnInit {
  /** The grid's accessible name. */
  readonly ariaLabel = input<string | null>(null);

  private readonly grid = inject(Grid);

  ngOnInit(): void {
    if ((typeof ngDevMode === 'undefined' || ngDevMode) && this.grid.softDisabled()) {
      throw new Error('fleetKitGrid is missing [softDisabled]="false"');
    }
  }
}
