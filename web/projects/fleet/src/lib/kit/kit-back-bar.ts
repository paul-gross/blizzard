import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The mobile drill-down's back affordance — a full-width 44px row with a chevron
 * and an underlined label. The one owner of that chrome for every screen either
 * shell drills into: the hub's chunk and artifact pages, and the runner panel's
 * chunk detail screen.
 *
 * Carries **no** navigation of its own, which is what lets one copy serve all
 * three: the caller wraps it in whatever fires — an `<a routerLink>` for a route,
 * a `<button>` for a shell that clears its own selection — so this stays inside
 * the kit's dependency floor (no `@angular/router`). `:host` is the row itself,
 * so the caller's wrapper contributes the hit target and this contributes the
 * height and the look.
 *
 * Reads as a link, not a section header: sentence case rather than the engraved
 * uppercase every panel label uses, cyan and underlined.
 */
@Component({
  selector: 'fleet-kit-back-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-back-bar.html',
  styleUrl: './kit-back-bar.css',
})
export class KitBackBar {
  /** What the row goes back to, in sentence case (`Board`, `Machine`, `ch_01H…`). */
  readonly label = input.required<string>();
}
