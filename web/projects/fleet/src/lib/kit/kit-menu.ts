import { ChangeDetectionStrategy, Component, TemplateRef, input } from '@angular/core';
import { CdkMenu, CdkMenuTrigger } from '@angular/cdk/menu';

/**
 * The quiet overflow menu's trigger (mobile polish feedback item 5,
 * `../../../docs/designs/mobile/core-flows.html`) — a small icon button that
 * opens its {@link KitMenuPanel} in a CDK overlay, closing on an outside
 * click, `Escape`, or a triggered item.
 *
 * Built on `@angular/cdk/menu` (issue #161): the CDK is already a dependency,
 * it is unstyled (so the token layer below applies untouched), and it carries
 * the menu semantics a home-grown popover lacks — roving focus, arrow-key
 * navigation, typeahead, and submenus.
 *
 * The trigger's own content is a `[trigger]`-selected projection, defaulting to
 * the classic `⋮` glyph — a caller wanting a different trigger (the shell's
 * profile menu projects {@link KitAvatar}, issue #132) marks its projected
 * element `trigger` rather than this component growing a variant input per
 * trigger shape. `aria-haspopup`/`aria-expanded` come from `CdkMenuTrigger`.
 *
 * The panel arrives as a {@link TemplateRef} rather than as projected content:
 * `CdkMenu` discovers its items through a content query, and a content query
 * does not reach across an `<ng-content>` boundary — a panel wrapped around a
 * projection slot would register **zero** items and lose every keyboard
 * behavior the CDK exists to provide. Passing the template keeps the panel and
 * its items in the caller's own view, where the query finds them.
 */
@Component({
  selector: 'fleet-kit-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger],
  template: `
    <button
      type="button"
      class="trigger"
      [cdkMenuTriggerFor]="menu()"
      [attr.aria-label]="ariaLabel()"
      [attr.data-testid]="testid()"
    >
      <ng-content select="[trigger]">⋮</ng-content>
    </button>
  `,
  styles: `
    :host {
      display: inline-flex;
    }
    .trigger {
      font-family: inherit;
      background: none;
      border: 1px solid transparent;
      color: var(--label);
      cursor: pointer;
      line-height: 1;
      padding: 3px 8px;
      border-radius: 3px;
    }
    .trigger:hover {
      color: var(--cyan);
      border-color: var(--line);
    }
    .trigger[aria-expanded='true'] {
      color: var(--cyan);
      background: var(--overlay-25);
    }
  `,
})
export class KitMenu {
  /** The panel this trigger opens — the `<ng-template>` holding a
   * {@link KitMenuPanel} and its items, declared in the caller's own view. */
  readonly menu = input.required<TemplateRef<unknown>>();

  /** The trigger button's accessible name. */
  readonly ariaLabel = input('Menu');

  /** The trigger button's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}

/**
 * A menu panel — the floating, token-styled surface a {@link KitMenu} trigger
 * (or a {@link KitMenuItem} opening a submenu) renders into. Carries `CdkMenu`
 * as a host directive, so it *is* the CDK menu: `role="menu"`, roving focus
 * across its item children, arrow/Home/End navigation, and `Escape`/left-arrow
 * to close back into its parent.
 *
 * Presentational: the panel owns chrome only, and every color resolves through
 * the design-token layer — the same `--panel`/`--bezel` surface the home-grown
 * popover this replaced carried, so the visual result is unchanged.
 */
@Component({
  selector: 'fleet-kit-menu-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  hostDirectives: [CdkMenu],
  host: {
    '[attr.data-testid]': 'testid()',
  },
  template: `<ng-content />`,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-width: 150px;
      margin-top: 4px;
      background: var(--panel);
      border: 1px solid var(--bezel);
      box-shadow: 0 8px 20px var(--overlay-40);
      padding: 4px 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      white-space: nowrap;
      outline: none;
    }
  `,
})
export class KitMenuPanel {
  /** The panel's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}
