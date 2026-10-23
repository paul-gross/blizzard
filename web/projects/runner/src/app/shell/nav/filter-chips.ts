import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitChip, KitSwatch } from 'fleet/shell';
import { KitGlyph } from 'fleet/runner-shell';

import type { FilterChoice } from './panel-filters.model';

/**
 * One filter control in the tab strip — its label, then a chip per choice, the current one
 * selected; patterned on the hub's project lens control. Presentational: the container
 * hands it the `choices` and the `current` value, and hears the chosen value on `choose`.
 *
 * The chips scroll inside the control rather than widen the row, so a host with many
 * runners or projects never pushes the strip past the viewport.
 */
@Component({
  selector: 'app-filter-chips',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip, KitGlyph, KitSwatch],
  templateUrl: './filter-chips.html',
  styleUrl: './filter-chips.css',
  host: { '[class.compact]': 'compact()' },
})
export class FilterChips {
  /** The control's name — its visible label and its group's accessible name. */
  readonly label = input.required<string>();

  /** The control's test id; each chip's is `<testid>-<value or all>`. */
  readonly testid = input.required<string>();

  readonly choices = input.required<readonly FilterChoice[]>();

  /** Whether it sits as its own row (mobile) rather than inline in the tab strip. */
  readonly compact = input(false);

  /** The chosen value, `null` for All. */
  readonly current = input.required<string | null>();

  /** The value chosen, `null` for All. */
  readonly choose = output<string | null>();

  protected onChoose(choice: FilterChoice): void {
    if (!choice.disabled && choice.value !== this.current()) this.choose.emit(choice.value);
  }
}
