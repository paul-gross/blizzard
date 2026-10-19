import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitPanel } from 'fleet';

import { LocalAsks } from '../../asks/app-asks';
import { HostPanel } from './host-panel';
import { HubPanel } from './hub-panel';

/**
 * The board's right rail, top to bottom: the hub the host's runners report to ({@link HubPanel}),
 * this host's subscriptions, runners and environments ({@link HostPanel}),
 * then the open asks. Each panel is a self-fetching container, so the rail owns nothing beyond
 * laying them out — filling a desktop column's height, or `stacked` in a phone's scrolling
 * column, where each panel sizes to its content.
 */
@Component({
  selector: 'app-board-rail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalAsks, HostPanel, HubPanel, KitPanel],
  templateUrl: './board-rail.html',
  styleUrl: './board-rail.css',
  host: { '[class.stacked]': 'stacked()' },
})
export class BoardRail {
  readonly openAskCount = input.required<number>();

  /** Size each panel to its content in a scrolling column rather than fill a column's height. */
  readonly stacked = input(false);
}
