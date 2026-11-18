import { Directive } from '@angular/core';
import { GridRow } from '@angular/aria/grid';

/**
 * One row of a {@link KitGrid} — put on the body `<tr>`. Carries Aria's `ngGridRow`, which registers the
 * row's {@link KitGridCell}s with the grid.
 */
@Directive({
  selector: '[fleetKitGridRow]',
  hostDirectives: [{ directive: GridRow, inputs: ['rowIndex'] }],
})
export class KitGridRow {}
