import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The tab-strip chrome shared by every tab strip in the app (blizzard#203) —
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
  template: `<ng-content />`,
  styles: `
    :host {
      display: flex;
      align-items: stretch;
      height: 32px;
      border-bottom: 1px solid var(--bezel);
      background: var(--header-lo);
      font-family: var(--mono);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
  `,
})
export class KitTabStrip {}

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
  template: `<ng-content />`,
  styles: `
    :host {
      display: flex;
      align-items: center;
      padding: 0 16px;
      background: transparent;
      border: none;
      border-right: 1px solid var(--line);
      color: var(--label);
      font-family: inherit;
      text-decoration: none;
      cursor: pointer;
    }
    :host(.active) {
      color: var(--amber-hi);
      background: var(--header-hi);
    }
  `,
})
export class KitTab {}
