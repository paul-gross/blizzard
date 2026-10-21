import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Which tier a tab strip sits at: `top` is the app's primary strip (amber
 * active tab), `sub` a strip nested inside a page (cyan active tab, a shorter
 * `--panel-deep` row) — so the hierarchy reads at a glance. */
export type KitTabVariant = 'top' | 'sub';

/**
 * The tab-strip chrome shared by every tab strip in the app —
 * the main nav's routed tabs and {@link KitTabs}' page-local tabs alike: 32px
 * tall, `--header-lo` ground, one `--bezel` line closing the bottom.
 *
 * An attribute-selector component, not a wrapped element — the pattern
 * `graph-diagram-node-shape.ts` established and `fleet/eslint.config.js`
 * permits, chosen here for the same reason {@link KitBackBar}'s doc comment
 * gives for its own no-wrapper stance: the caller's own element (a `<nav>`, a
 * `<div role="tablist">`) is what needs the router bindings or ARIA role, so
 * this directive contributes only the look, never an element of its own.
 */
@Component({
  selector: '[fleetKitTabStrip]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-tab-strip.html',
  styleUrl: './kit-tab-strip.css',
  host: {
    '[class.sub]': "variant() === 'sub'",
  },
})
export class KitTabStrip {
  readonly variant = input<KitTabVariant>('top');
}

/**
 * One tab's chrome inside a {@link KitTabStrip} — the amber-hi-on-`--header-hi`
 * active treatment and the `--line` right divider the main nav defined, now
 * the one copy the nav's routed tabs and {@link KitTabs}' page-local tabs both
 * wear. The caller supplies the element and toggles its own `.active` class;
 * this directive carries no navigation or selection state of its own.
 */
@Component({
  selector: '[fleetKitTab]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-tab.html',
  styleUrl: './kit-tab.css',
  host: {
    '[class.sub]': "variant() === 'sub'",
  },
})
export class KitTab {
  /** Must match the enclosing {@link KitTabStrip}'s `variant`. */
  readonly variant = input<KitTabVariant>('top');

  /** How many the tab holds, shown after its label; nothing renders while `null`, but `0` does. */
  readonly count = input<number | null>(null);

  /** The count span's `data-testid`, or `null` for none (the name {@link KitPanel} gives the same input). */
  readonly countTestid = input<string | null>(null);

  /** Reads the count in the amber-hi attention color, for a count of things waiting on a person. */
  readonly countHot = input(false);
}
