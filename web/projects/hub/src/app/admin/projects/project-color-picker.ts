import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitChip, KitColorInput, KitSwatch } from 'fleet';

/**
 * The palette as a row of choice chips, each a swatch followed by its color name as text, the project's current color
 * selected, and a Custom chip after them that is selected when the current color is none of the palette's and reveals a
 * color input seeded with it. Presentational: the colors, the current one and whether a save is pending in, the chosen
 * color out — a palette name, or the `#rrggbb` the input committed, for the hub to accept or refuse.
 */
@Component({
  selector: 'app-project-color-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip, KitColorInput, KitSwatch],
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

  /** Whether the person opened the custom input on a palette color; a custom current color opens it on its own. */
  protected readonly customRequested = signal(false);

  /** The current color is outside the palette, so it is a custom one. */
  protected readonly isCustom = computed(() => !this.colors().includes(this.current()));
  protected readonly customOpen = computed(() => this.isCustom() || this.customRequested());

  protected onPalette(color: string): void {
    this.customRequested.set(false);
    this.choose.emit(color);
  }
}
