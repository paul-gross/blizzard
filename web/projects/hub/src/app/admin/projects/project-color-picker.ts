import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitChip, KitSwatch } from 'fleet';

/**
 * The palette as a row of choice chips, each a swatch followed by its color name as text, the project's current color
 * selected. Presentational: the colors, the current one and whether a save is pending in, the chosen color out.
 */
@Component({
  selector: 'app-project-color-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip, KitSwatch],
  templateUrl: './project-color-picker.html',
  styleUrl: './project-color-picker.css',
})
export class ProjectColorPicker {
  /** The palette, by name. */
  readonly colors = input.required<readonly string[]>();
  /** The project's current color. */
  readonly current = input.required<string>();
  /** Set while an edit is pending, so a second pick cannot race the first. */
  readonly disabled = input(false);

  readonly choose = output<string>();
}
