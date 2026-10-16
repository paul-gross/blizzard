import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The whole-tile navigation link — an attribute component on the caller's own `<a>`, so the
 * host *is* the single anchor and the projected tile content sits inside it. Owns the tile's
 * interactive chrome: hover background, a `:focus-visible` ring around the full tile, and a
 * pressed state.
 *
 * Carries **no** navigation of its own, which keeps the kit inside its dependency floor (no
 * `@angular/router`): the caller puts `routerLink` (or `href`) on the same `<a>` and names it
 * with `aria-label`, which makes a screen reader announce one name rather than every fact
 * inside. The projected content must hold no interactive element of its own.
 */
@Component({
  selector: 'a[fleetKitLinkTile]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-link-tile.html',
  styleUrl: './kit-link-tile.css',
})
export class KitLinkTile {}
