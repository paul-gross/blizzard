import type { FlowFactView, FlowRecordPage, WorkItemsView } from 'fleet';

import { flowRecordRows, flowRecordTitle } from './flow-record.model';

const fact = (id: number, over: Partial<FlowFactView> = {}): FlowFactView => ({
  id,
  flow_id: 'flw_1',
  chunk_id: 'ch_a',
  submission_id: 'sub_a',
  kind: 'submitted',
  outcome: null,
  revision: null,
  at: '2026-10-29T11:00:00Z',
  ...over,
});

describe('flowRecordRows', () => {
  it('lists every page\'s facts in the order the pages hold them, newest first', () => {
    const pages: FlowRecordPage[] = [
      { facts: [fact(3, { kind: 'landed' }), fact(2, { kind: 'turn-ended', outcome: 'landed' })], next_before: 2 },
      { facts: [fact(1)], next_before: null },
    ];
    const rows = flowRecordRows(pages, new Map(), Date.parse('2026-10-29T12:00:00Z'));
    expect(rows.map((row) => row.id)).toEqual([3, 2, 1]);
    expect(rows.map((row) => row.kindLabel)).toEqual(['landed', 'turn ended', 'submitted']);
    expect(rows[1].outcome).toBe('landed');
  });

  it('reads a fact kind this build does not know raw', () => {
    const rows = flowRecordRows([{ facts: [fact(1, { kind: 'held' as FlowFactView['kind'] })], next_before: null }], new Map(), 0);
    expect(rows[0].kindLabel).toBe('held');
  });

  it('titles a chunk once the board has read its work items', () => {
    const items = new Map<string, WorkItemsView>([['ch_a', { items: [{ title: 'Migrate the store' } as WorkItemsView['items'][number]] }]]);
    expect(flowRecordRows([{ facts: [fact(1)], next_before: null }], items, 0)[0].title).toBe('Migrate the store');
    expect(flowRecordRows(undefined, items, 0)).toEqual([]);
  });
});

describe('flowRecordTitle', () => {
  it('names the flow by its lane title once read, else by its id', () => {
    expect(flowRecordTitle('flw_1', undefined)).toBe('flw_1');
    expect(
      flowRecordTitle('flw_1', {
        flow_id: 'flw_1', resource: 'r', repository_id: 'repo_1', repository_name: 'blizzard', repository_slug: null,
        branch: 'master', turn: null, line: [], last_verdict: null, last_landing: null,
      }),
    ).toBe('blizzard · master');
  });
});
