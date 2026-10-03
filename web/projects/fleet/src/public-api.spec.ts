import { injectHubChunkWorkItemsQuery } from 'fleet';

/**
 * The chunk work-items query is asserted at the `fleet` path-mapped barrel a
 * consumer actually imports from, so a regression that drops the root re-export
 * line is caught.
 */
describe('fleet public API — chunk exports', () => {
  it('reaches injectHubChunkWorkItemsQuery from the fleet barrel', () => {
    expect(typeof injectHubChunkWorkItemsQuery).toBe('function');
  });
});
