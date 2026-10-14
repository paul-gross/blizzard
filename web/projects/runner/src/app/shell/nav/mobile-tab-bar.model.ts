import type { MobileTabItem } from 'fleet';

/** One of the active runner's own tabs — routed to its page, or inert on a host page,
 * where no runner is active. */
function runnerTab(runnerId: string | null, testid: string, label: string, page: string): MobileTabItem {
  return runnerId === null
    ? { testid, label, inert: true }
    : { testid, label, route: `/runners/${runnerId}/${page}`, queryParamsHandling: 'preserve' };
}

/** The mobile tab bar's tabs: `Host`, then the active runner's `Board`, the always-inert
 * `Asks` carrying `askCount`, and the runner's `Events` and `Machine`. */
export function mobileTabItems(runnerId: string | null, askCount: number): readonly MobileTabItem[] {
  return [
    { testid: 'tab-host', label: 'Host', route: '/', exact: true },
    runnerTab(runnerId, 'tab-board', 'Board', 'board'),
    { testid: 'tab-asks-runner', label: 'Asks', inert: true, badge: askCount, badgeTestid: 'tab-asks-runner-badge' },
    runnerTab(runnerId, 'tab-events', 'Events', 'events'),
    runnerTab(runnerId, 'tab-machine', 'Machine', 'machine'),
  ];
}
