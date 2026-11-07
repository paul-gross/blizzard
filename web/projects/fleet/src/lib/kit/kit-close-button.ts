import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitButton } from './kit-button';

/**
 * The close control — a `KitButton` of `size="icon"` holding a drawn X, so the glyph
 * centres on its own ink rather than a fallback font's line box. The X is decorative;
 * the button's name is `ariaLabel`. As with `KitButton`, the click bubbles to the
 * host: a caller binds `(click)` on `<fleet-kit-close-button>` directly.
 */
@Component({
  selector: 'fleet-kit-close-button',
  imports: [KitButton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-close-button.html',
  styleUrl: './kit-close-button.css',
})
export class KitCloseButton {
  readonly ariaLabel = input('Close');
  readonly testid = input<string | null>(null);
}
