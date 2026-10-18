import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * A project's color as a small rounded square — the palette name resolves to its `--project-<name>` token, and an
 * unknown name falls back to the dim label color. With a `label` it is an image named by it; without one it is
 * decorative, for a place where the project's name sits beside it. Presentational and not interactive: it holds no
 * data client and knows no palette, only the token naming rule.
 */
@Component({
  selector: 'fleet-kit-swatch',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-swatch.html',
  styleUrl: './kit-swatch.css',
})
export class KitSwatch {
  /** The palette name to show. */
  readonly color = input.required<string>();

  /** The accessible name and tooltip; `null` marks the swatch decorative. */
  readonly label = input<string | null>(null);

  protected readonly fill = computed(() => `var(--project-${this.color()}, var(--label-dim))`);
}
