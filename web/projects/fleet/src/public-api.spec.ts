import { injectHubChunkWorkItemsQuery, injectSetChunkGraphMutation } from 'fleet';

/**
 * The chunk work-items query and graph edit mutation are asserted at the `fleet`
 * path-mapped barrel a consumer actually imports from, not just the `chunks/`
 * sub-barrel, so a regression that drops the root re-export line is caught too.
 */
describe('fleet public API — chunk exports', () => {
  it('reaches injectHubChunkWorkItemsQuery from the fleet barrel', () => {
    expect(typeof injectHubChunkWorkItemsQuery).toBe('function');
  });

  it('reaches the chunk graph edit mutation from the fleet barrel', () => {
    expect(typeof injectSetChunkGraphMutation).toBe('function');
  });
});
