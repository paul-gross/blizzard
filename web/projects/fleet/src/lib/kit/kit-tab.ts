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
      /* The strip is the one that gives, never the page. Three uppercase
         letter-spaced labels (GENERAL/ARTIFACTS/TRANSCRIPTS) measure ~315px —
         wider than a 320px phone's content box — and a plain non-wrapping flex
         row pushed that overflow all the way up to the page, which is what
         chunk-detail-page.shell-sweep.spec.ts caught at width 320. Scrolling
         here absorbs it instead: min-width: 0 lets the strip be narrower than
         its content, and overflow-y: hidden keeps the implicit auto that
         overflow-x would otherwise force from clipping the 32px row. */
      min-width: 0;
      overflow-x: auto;
      overflow-y: hidden;
      /* A scrollbar inside a 32px row would eat the tabs it is scrolling. */
      scrollbar-width: none;
    }
    :host::-webkit-scrollbar {
      width: 0;
      height: 0;
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
      /* A tab is its label; it neither squeezes nor breaks. Explicit because
         the strip scrolls now (see KitTabStrip) — flex's default shrink would
         otherwise compress the labels toward illegibility before the strip
         ever reached the scroll it gained for exactly this case. */
      flex: none;
      white-space: nowrap;
    }
    /* Phone widths: the tracking that makes these labels legible on a monitor is
       what pushes them past the viewport here, so the gutters give first — the
       strip can still scroll if a fourth tab arrives, but three fit outright. */
    @media (max-width: 420px) {
      :host {
        padding: 0 10px;
      }
    }
    :host(.active) {
      color: var(--amber-hi);
      background: var(--header-hi);
    }
  `,
})
export class KitTab {}
