import { Directive } from '@angular/core';
import { GridCell } from '@angular/aria/grid';

/**
 * One cell of a {@link KitGridRow} — put on the `<td>`. Carries Aria's `ngGridCell`; a cell holding a
 * {@link KitGridWidget} is reached through the widget's native control, a text cell takes focus itself.
 * A cell with no control to work (an empty actions column) sets `disabled`, so arrow navigation skips it.
 *
 * Aria's `GridCell` declares no host marker, yet the grid resolves an event's cell with
 * `closest('[ngGridCell]')`, so the literal `ngGridCell` attribute is set here; without it a keydown on a
 * nested native button resolves to no cell.
 */
@Directive({
  selector: '[fleetKitGridCell]',
  hostDirectives: [{ directive: GridCell, inputs: ['disabled', 'colSpan', 'rowSpan'] }],
  host: { ngGridCell: '' },
})
export class KitGridCell {}
