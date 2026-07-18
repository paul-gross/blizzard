import { injectHubChunkPmItemsQuery, injectSetChunkGraphMutation, injectSetChunkModelMutation } from 'fleet';

/**
 * The two exports #82's sub-barrel rewrite adds (AC): `chunk-pm-items.query.ts` and
 * `edit.mutations.ts` were reachable only from inside `chunk-detail.ts`/the panel
 * before this phase, absent from `public-api.ts`. Asserted here at the `fleet`
 * path-mapped barrel a consumer actually imports from, not just the `chunks/`
 * sub-barrel, so a regression that drops the root re-export line is caught too.
 */
describe('fleet public API — the previously-missing chunk exports (issue #82)', () => {
  it('reaches injectHubChunkPmItemsQuery from the fleet barrel', () => {
    expect(typeof injectHubChunkPmItemsQuery).toBe('function');
  });

  it('reaches the chunk edit mutations from the fleet barrel', () => {
    expect(typeof injectSetChunkGraphMutation).toBe('function');
    expect(typeof injectSetChunkModelMutation).toBe('function');
  });
});
