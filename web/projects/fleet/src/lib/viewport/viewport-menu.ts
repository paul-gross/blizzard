import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';

import { KitMenuItemRadio, KitMenuPanel } from '../kit';
import { ViewportService, type ViewportOverride } from './viewport-service';

interface AppearanceOption {
  readonly value: ViewportOverride;
  readonly label: string;
  readonly testid: string;
}

const OPTIONS: readonly AppearanceOption[] = [
  { value: 'auto', label: 'Auto', testid: 'viewport-menu-auto' },
  { value: 'mobile', label: 'Mobile', testid: 'viewport-menu-mobile' },
  { value: 'desktop', label: 'Desktop', testid: 'viewport-menu-desktop' },
];

/**
 * The appearance submenu (issue #161) — the viewport override as a real
 * `role="menuitemradio"` group rather than the always-visible chip row the
 * shells used to show inline, so the shell menus read as menus and the choice
 * is one arrow-key traversal away.
 *
 * This component is the submenu **panel**, not the item that opens it: the item
 * belongs to the caller's own panel, because `CdkMenu` finds its items by a
 * content query that stops at a child component's template boundary. A shell
 * therefore declares a `<fleet-kit-menu-item [cdkMenuTriggerFor]="appearance"
 * submenu>` of its own and points that template at this component — the four
 * lines that keep the item keyboard-reachable from the parent menu.
 *
 * The trailing row reads the *effective* mode (`ViewportService.mode`), which
 * differs from the checked option whenever the override is `'auto'`.
 */
@Component({
  selector: 'fleet-viewport-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenuItemRadio, KitMenuPanel],
  templateUrl: './viewport-menu.html',
  styleUrl: './viewport-menu.css',
})
export class ViewportMenu {
  protected readonly viewport = inject(ViewportService);
  protected readonly options = OPTIONS;

  /** The submenu panel's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}
