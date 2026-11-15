import { describe, expect, it } from 'vitest';
import { DESKTOP_TABS, MOBILE_TABS, MOBILE_TAB_ITEMS } from './navigation.model';

describe('shell route tabs', () => {
  it('keeps Graphs desktop-only without changing mobile order', () => {
    expect(DESKTOP_TABS.map((tab) => tab.label)).toEqual(['Board', 'Delivery', 'Fleet', 'Graphs', 'Gardening', 'Events']);
    expect(MOBILE_TABS.map((tab) => tab.label)).toEqual(['Board', 'Delivery', 'Fleet', 'Gardening', 'Events']);
    expect(MOBILE_TAB_ITEMS).toEqual(MOBILE_TABS.map((tab) => ({
      testid: `tab-${tab.key}`, label: tab.label, route: tab.route,
    })));
  });
});
