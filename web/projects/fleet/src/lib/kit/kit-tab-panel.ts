import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { TabPanel } from '@angular/aria/tabs';

/**
 * One panel a {@link KitTabs} strip switches to — `<div [fleetKitTabPanel]="value">`
 * inside a {@link KitTabGroup}, holding a `<ng-template fleetKitTabContent>` with the
 * panel's body. Carries Aria's `ngTabPanel`: the `tabpanel` role and the pairing with the tab
 * of the same value.
 *
 * Aria only marks a hidden panel `inert`; this part is what keeps it from rendering a box,
 * through an inline `display: none` no consumer stylesheet overrides. A visible panel is
 * `display: contents`, so its content lays out exactly as a direct child of the panel's
 * parent would.
 */
@Component({
  selector: '[fleetKitTabPanel]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  hostDirectives: [{ directive: TabPanel, inputs: ['value: fleetKitTabPanel'] }],
  host: { '[style.display]': "panel.visible() ? null : 'none'" },
  styleUrl: './kit-tab-panel.css',
  templateUrl: './kit-tab-panel.html',
})
export class KitTabPanel {
  protected readonly panel = inject(TabPanel);
}
