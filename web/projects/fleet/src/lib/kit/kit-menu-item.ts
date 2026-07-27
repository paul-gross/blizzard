import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';
import { CdkMenuItem, CdkMenuItemRadio } from '@angular/cdk/menu';

/*
 * The two menu-item shapes a {@link KitMenuPanel} holds (issue #161).
 *
 * Both wear their CDK directive as a **host directive** rather than rendering
 * one inside their own template: `CdkMenu`'s item content query matches
 * directives declared in the caller's view, and a `cdkMenuItem` buried in a
 * child component's template is invisible to it. On the host element the
 * directive is where the query looks, and the component still gets to own the
 * token styling through `:host` — which a plain directive could not carry.
 */

/**
 * One menu item — the action row inside a {@link KitMenuPanel}. `(triggered)`
 * fires on click, `Enter`, or `Space`; the CDK closes the menu stack for a
 * plain item and leaves it open for one that opens a submenu.
 *
 * A submenu item is this same component with `[cdkMenuTriggerFor]` bound on it
 * (the CDK's own idiom) plus `[submenu]="true"` for the trailing chevron —
 * right-arrow opens it, left-arrow returns.
 */
@Component({
  selector: 'fleet-kit-menu-item',
  changeDetection: ChangeDetectionStrategy.OnPush,
  hostDirectives: [
    {
      directive: CdkMenuItem,
      inputs: ['cdkMenuItemDisabled: disabled'],
      outputs: ['cdkMenuItemTriggered: triggered'],
    },
  ],
  host: {
    '[attr.data-testid]': 'testid()',
  },
  template: `
    <span class="label"><ng-content /></span>
    @if (submenu()) {
      <span class="chevron" aria-hidden="true">›</span>
    }
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 12px;
      color: var(--text);
      cursor: pointer;
      outline: none;
      user-select: none;
    }
    :host(:hover),
    :host(:focus) {
      background: var(--overlay-25);
      color: var(--cyan);
    }
    :host([aria-expanded='true']) {
      color: var(--cyan);
    }
    :host([aria-disabled='true']) {
      opacity: 0.4;
      cursor: default;
    }
    .label {
      flex: 1;
    }
    .chevron {
      color: var(--label);
    }
  `,
})
export class KitMenuItem {
  /** The item's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);

  /** Whether this item opens a submenu — draws the trailing chevron. The
   * submenu itself is wired by binding `[cdkMenuTriggerFor]` on this element. */
  readonly submenu = input(false, { transform: booleanAttribute });
}

/**
 * One radio menu item — a `role="menuitemradio"` row whose siblings in the same
 * {@link KitMenuPanel} form a single-selection group, the shape a closed
 * either/or choice (the appearance switcher's auto/mobile/desktop) takes inside
 * a menu.
 *
 * The selection marker is driven straight off `aria-checked`, the attribute the
 * CDK already maintains, so the visual state and the state assistive tech reads
 * can never disagree.
 */
@Component({
  selector: 'fleet-kit-menu-item-radio',
  changeDetection: ChangeDetectionStrategy.OnPush,
  hostDirectives: [
    {
      directive: CdkMenuItemRadio,
      inputs: ['cdkMenuItemChecked: checked', 'cdkMenuItemDisabled: disabled'],
      outputs: ['cdkMenuItemTriggered: triggered'],
    },
  ],
  host: {
    '[attr.data-testid]': 'testid()',
  },
  template: `
    <span class="tick" aria-hidden="true">•</span>
    <span class="label"><ng-content /></span>
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 12px;
      color: var(--text);
      cursor: pointer;
      outline: none;
      user-select: none;
    }
    :host(:hover),
    :host(:focus) {
      background: var(--overlay-25);
      color: var(--cyan);
    }
    :host([aria-checked='true']) {
      color: var(--amber-hi);
    }
    .tick {
      width: 1ch;
      visibility: hidden;
    }
    :host([aria-checked='true']) .tick {
      visibility: visible;
    }
  `,
})
export class KitMenuItemRadio {
  /** The item's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}
