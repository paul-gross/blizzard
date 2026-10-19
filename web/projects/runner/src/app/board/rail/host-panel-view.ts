import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitBeacon, KitGlyph, KitSlotBar, SubscriptionBlocks, type SubscriptionLine } from 'fleet';

import type { HostPanelPool, HostPanelRunner } from './host-panel.model';

/**
 * The board rail's host panel — the presentational half, laid out as the hub's board rail
 * shows a host: each subscription's usage windows as a line of blocks, then one compact row per runner (its light,
 * name and brake glyphs, over a line of agent blocks per binding), then a grey line of blocks per workspace, one
 * per environment in its own order, lit where held and naming its holder in its tooltip.
 *
 * Presentational only (`bzh:frontend-container-presentational`): {@link HostPanel} folds the
 * host's reads into these inputs.
 */
@Component({
  selector: 'app-host-panel-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon, KitGlyph, KitSlotBar, SubscriptionBlocks],
  templateUrl: './host-panel-view.html',
  styleUrl: './host-panel-view.css',
})
export class HostPanelView {
  readonly subscriptions = input.required<readonly SubscriptionLine[]>();
  readonly runners = input.required<readonly HostPanelRunner[]>();
  readonly pools = input.required<readonly HostPanelPool[]>();
}
