import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitPanel } from 'fleet';

import { HostEnvironments } from './host-environments';
import { HostRunners } from './host-runners';
import { HostSubscriptions } from './host-subscriptions';

/**
 * The host page (`/`) — what belongs to the host rather than to one runner: the runners
 * it serves or holds halted, its environments grouped by workspace, and its
 * subscriptions with the runners spending them. Each panel is a self-fetching container
 * over the host's own reads, so this page owns nothing beyond laying them out: side by
 * side on a wide screen, stacked on a narrow one.
 */
@Component({
  selector: 'app-host-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostEnvironments, HostRunners, HostSubscriptions, KitPanel],
  templateUrl: './host-page.html',
  styleUrl: './host-page.css',
})
export class HostPage {}
