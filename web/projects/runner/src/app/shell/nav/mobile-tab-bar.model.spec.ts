import { mobileTabItems } from './mobile-tab-bar.model';

describe('mobileTabItems', () => {
  it('routes Board, Events, and Host to their flat pages, with Asks inert between them', () => {
    const items = mobileTabItems(2);
    expect(items.map((item) => [item.testid, item.route ?? null, item.inert ?? false])).toEqual([
      ['tab-board', '/board', false],
      ['tab-asks-runner', null, true],
      ['tab-events', '/events', false],
      ['tab-host', '/host', false],
    ]);
    expect(items[1].badge).toBe(2);
  });

  it('carries the filters and the selection across every routed tab', () => {
    expect(
      mobileTabItems(0)
        .filter((item) => item.route)
        .map((item) => item.queryParamsHandling),
    ).toEqual(['merge', 'merge', 'merge']);
  });
});
