import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { isHexColor, legibleOnDark, projectColorValue } from './kit-swatch-color';

/**
 * A project's color as a small rounded square — the palette name resolves to its `--project-<name>` token, and an
 * unknown name falls back to the dim label color. A `#rrggbb` custom color is drawn as itself, lifted where it would
 * sink into the dark surfaces. With a `label` it is an image named by it; without one it is
 * decorative, for a place where the project's name sits beside it. Presentational and not interactive: it holds no
 * data client and knows no palette, only the token naming rule and the `#` shape of a CSS color literal.
 */
@Component({
  selector: 'fleet-kit-swatch',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-swatch.html',
  styleUrl: './kit-swatch.css',
})
export class KitSwatch {
  /** The palette name or `#rrggbb` color to show. */
  readonly color = input.required<string>();

  /** The accessible name and tooltip; `null` marks the swatch decorative. */
  readonly label = input<string | null>(null);

  protected readonly fill = computed(() => {
    const color = this.color();
    return isHexColor(color) ? legibleOnDark(color) : projectColorValue(color);
  });
}
