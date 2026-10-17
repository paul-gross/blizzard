import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** The glyphs the kit draws: `pause`, bars in a square — a machine that stopped itself; `stop`,
 * a barred octagon — a stop someone else imposed. */
export type KitGlyphName = 'pause' | 'stop';

/**
 * A small inline-SVG glyph for a row too dense for a word or a badge. It strokes in
 * `currentColor`, so the consumer colours it, and sizes to `--kit-glyph-size` (12px by default).
 * `label` is its tooltip and accessible name — a glyph never stands without one.
 *
 * Presentational, input-only.
 */
@Component({
  selector: 'fleet-kit-glyph',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-glyph.html',
  styleUrl: './kit-glyph.css',
  host: { '[attr.data-glyph]': 'glyph()', '[attr.title]': 'label()' },
})
export class KitGlyph {
  readonly glyph = input.required<KitGlyphName>();

  /** The tooltip and accessible name. */
  readonly label = input.required<string>();
}
