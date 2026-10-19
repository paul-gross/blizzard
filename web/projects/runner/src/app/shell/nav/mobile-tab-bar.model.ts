import type { MobileTabItem } from 'fleet';

/** One of the panel's routed tabs, carrying the filters and the board's selection across. */
function pageTab(testid: string, label: string, page: string): MobileTabItem {
  return { testid, label, route: `/${page}`, queryParamsHandling: 'merge' };
}

/** The mobile tab bar's tabs: `Board`, the always-inert `Asks` carrying `askCount`, then
 * `Events` and `Host` — the same on every page and under every filter. */
export function mobileTabItems(askCount: number): readonly MobileTabItem[] {
  return [
    pageTab('tab-board', 'Board', 'board'),
    { testid: 'tab-asks-runner', label: 'Asks', inert: true, badge: askCount, badgeTestid: 'tab-asks-runner-badge' },
    pageTab('tab-events', 'Events', 'events'),
    pageTab('tab-host', 'Host', 'host'),
  ];
}
