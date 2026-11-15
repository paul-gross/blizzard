/** Route tabs shared by the settled shell and its noninteractive loading chrome. */
export const DESKTOP_TABS = [
  { key: 'board', label: 'Board', route: '/board' },
  { key: 'delivery', label: 'Delivery', route: '/delivery' },
  { key: 'fleet', label: 'Fleet', route: '/fleet' },
  { key: 'graphs', label: 'Graphs', route: '/graphs' },
  { key: 'gardening', label: 'Gardening', route: '/gardening' },
  { key: 'events', label: 'Events', route: '/events' },
] as const;

export const MOBILE_TABS = DESKTOP_TABS.filter((tab) => tab.key !== 'graphs');

export const MOBILE_TAB_ITEMS = MOBILE_TABS.map((tab) => ({
  testid: `tab-${tab.key}`,
  label: tab.label,
  route: tab.route,
}));
