import { injectHubChunkWorkItemsQuery, injectSetChunkGraphMutation } from 'fleet';

/**
 * The two exports #82's sub-barrel rewrite adds (AC): `chunk-work-items.query.ts` and
 * `edit.mutations.ts` were reachable only from inside `chunk-detail.ts`/the panel
 * before this phase, absent from `public-api.ts`. Asserted here at the `fleet`
 * path-mapped barrel a consumer actually imports from, not just the `chunks/`
 * sub-barrel, so a regression that drops the root re-export line is caught too.
 */
describe('fleet public API — the previously-missing chunk exports (issue #82)', () => {
  it('reaches injectHubChunkWorkItemsQuery from the fleet barrel', () => {
    expect(typeof injectHubChunkWorkItemsQuery).toBe('function');
  });

  it('reaches the chunk graph edit mutation from the fleet barrel', () => {
    // The model edit mutation stood beside it until issue #144 retired `Chunk.model`
    // and left the replacing defaults with no web editing surface.
    expect(typeof injectSetChunkGraphMutation).toBe('function');
  });
});
