import { ChangeDetectionStrategy, Component, Directive, booleanAttribute, contentChild, input } from '@angular/core';
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
 * Marks the element a consumer projects into {@link KitMenuItem}'s subtitle
 * slot — the `fleetKitMenuItemSubtitle` attribute is both the projection
 * selector and this directive's own, the same marker-directive shape
 * {@link KitPanelHeader} (`kit-panel.ts`) already establishes for a slot. It
 * carries no behavior. A projection slot, not a string input: `KitMenuItem`'s
 * own template has no CDK content query of its own to collide with (the
 * `hostDirectives`-based `CdkMenuItem` wiring above governs `CdkMenu`'s query
 * for *items*, not what an item itself projects), and a slot keeps an
 * interpolated runner/node name in the caller's own view rather than forcing
 * it through an input. Import it alongside `KitMenuItem` wherever a
 * `fleetKitMenuItemSubtitle` element is projected.
 */
@Directive({ selector: '[fleetKitMenuItemSubtitle]' })
export class KitMenuItemSubtitle {}

/**
 * One menu item — the action row inside a {@link KitMenuPanel}. `(triggered)`
 * fires on click, `Enter`, or `Space`; the CDK closes the menu stack for a
 * plain item and leaves it open for one that opens a submenu.
 *
 * A submenu item is this same component with `[cdkMenuTriggerFor]` bound on it
 * (the CDK's own idiom) plus `[submenu]="true"` for the trailing chevron —
 * right-arrow opens it, left-arrow returns.
 *
 * **A submenu parent opens on hover and toggles on click**, which is stock
 * `@angular/cdk/menu` behavior we take deliberately rather than by omission: for
 * a mouse the pointer landing on the row already opened the submenu, so the
 * click that follows shuts it again. Keyboard (right-arrow) and touch (tap, with
 * no hover to have opened it first) both do the obvious thing, and touch is what
 * the shells' narrow widths actually get. Overriding it means re-entering the
 * CDK's open/close bookkeeping from outside, and an attempt to do so did not
 * hold up under test — so the behavior is pinned by the spec below instead of
 * papered over. Revisit with a real fix if the mouse gesture starts to matter;
 * do not reach for an ordering trick.
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
  templateUrl: './kit-menu-item.html',
  styleUrl: './kit-menu-item.css',
})
export class KitMenuItem {
  /** The item's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);

  /** Whether this item opens a submenu — draws the trailing chevron. The
   * submenu itself is wired by binding `[cdkMenuTriggerFor]` on this element. */
  readonly submenu = input(false, { transform: booleanAttribute });

  /** Whether a consumer projected a {@link KitMenuItemSubtitle}-marked element on
   * this render — observed, not declared, the same `contentChild` shape
   * {@link KitPanelHeader}'s own slot-occupancy query uses (`kit-panel.ts`), so an
   * item with nothing projected stacks no empty subtitle line. */
  protected readonly subtitleContent = contentChild(KitMenuItemSubtitle);
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
  templateUrl: './kit-menu-item-radio.html',
  styleUrl: './kit-menu-item-radio.css',
})
export class KitMenuItemRadio {
  /** The item's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}
