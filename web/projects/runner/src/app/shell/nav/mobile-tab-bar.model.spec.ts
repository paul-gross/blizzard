import { mobileTabItems } from './mobile-tab-bar.model';

describe('mobileTabItems', () => {
  it('routes the runner tabs to the active runner’s pages', () => {
    const items = mobileTabItems('rn_1', 2);
    expect(items.map((item) => [item.testid, item.route ?? null, item.inert ?? false])).toEqual([
      ['tab-host', '/', false],
      ['tab-board', '/runners/rn_1/board', false],
      ['tab-asks-runner', null, true],
      ['tab-events', '/runners/rn_1/events', false],
      ['tab-machine', '/runners/rn_1/machine', false],
    ]);
    expect(items[0].exact).toBe(true);
    expect(items[2].badge).toBe(2);
  });

  it('renders the runner tabs inert on a host page', () => {
    const items = mobileTabItems(null, 0);
    expect(items.filter((item) => item.inert).map((item) => item.testid)).toEqual([
      'tab-board',
      'tab-asks-runner',
      'tab-events',
      'tab-machine',
    ]);
  });
});
