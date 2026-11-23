import { workItemEntry } from '../../testing/builders/chunk.translate';
import { fromWorkItemsView } from './work-items.translate';

describe('fromWorkItemsView', () => {
  it('translates empty and partially resolved reads', () => {
    expect(fromWorkItemsView({ items: [] })).toEqual({ items: [] });
    const wire = workItemEntry({ title: null, body: null, error: 'unavailable', comments: ['message'] });
    const result = fromWorkItemsView({ items: [wire] });
    expect(result.items).toEqual([wire]);
    expect(result.items[0]).not.toBe(wire);
    expect(result.items[0].comments).not.toBe(wire.comments);
  });

  it('preserves the source classification and resolved author', () => {
    const author = { chunk_id: 'ch_1', kind: 'fleet', login: null, node_name: 'build', runner_id: 'rn_1', runner_name: 'runner', user_id: null };
    const wire = workItemEntry({ author, hub_source: true, stated_priority: 'high' });
    const result = fromWorkItemsView({ items: [wire] });
    expect(result.items[0]).toEqual({ ...wire, stated_priority: { kind: 'high' } });
    expect(result.items[0].author).not.toBe(author);
  });

  it('retains an unrecognized priority without treating it as a known one', () => {
    const wire = workItemEntry({ stated_priority: 'future' as 'high' });
    expect(fromWorkItemsView({ items: [wire] }).items[0].stated_priority).toEqual({ kind: 'unknown', raw: 'future' });
  });
});
