import type { RunRowView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { mintedAtFor, runListRows, summedCounts } from './gardening-runs-state.model';

const DELIVERED_RUN: RunRowView = {
  chunk_id: 'ch_1',
  routine_name: 'nightly',
  scope_slug: 'blizzard',
  mode: 'full',
  minted_at: '2026-01-10T00:00:00Z',
  outcome: 'done',
  escalation: null,
  delivered: [
    { finding_set_id: 'fins_1', revisions: { blizzard: 'abc123' }, measurement: '3 findings', added_count: 1, observed_count: 11, gone_count: 0 },
    { finding_set_id: 'fins_2', revisions: { blizzard: 'def456' }, measurement: null, added_count: 0, observed_count: 0, gone_count: 2 },
  ],
};

const ESCALATED_RUN: RunRowView = {
  chunk_id: 'ch_2',
  routine_name: 'nightly',
  scope_slug: 'web',
  mode: 'delta',
  minted_at: '2026-01-11T00:00:00Z',
  outcome: 'needs_human',
  escalation: { node_name: 'survey', takeover_command: 'blizzard hub chunk takeover ch_2', wrapped_takeover_command: '' },
  delivered: [],
};

describe('summedCounts', () => {
  it('answers null for a run that delivered no set', () => {
    expect(summedCounts([])).toBeNull();
  });

  it('sums each leg across every delivered set', () => {
    expect(summedCounts(DELIVERED_RUN.delivered)).toEqual({ added: 1, observed: 11, gone: 2 });
  });
});

describe('runListRows', () => {
  it('maps each run in order, reading escalated off a non-null escalation', () => {
    expect(runListRows([DELIVERED_RUN, ESCALATED_RUN])).toEqual([
      {
        chunkId: 'ch_1',
        routineName: 'nightly',
        scopeSlug: 'blizzard',
        mode: 'full',
        mintedAt: '2026-01-10T00:00:00Z',
        outcome: 'done',
        escalated: false,
        counts: { added: 1, observed: 11, gone: 2 },
      },
      {
        chunkId: 'ch_2',
        routineName: 'nightly',
        scopeSlug: 'web',
        mode: 'delta',
        mintedAt: '2026-01-11T00:00:00Z',
        outcome: 'needs_human',
        escalated: true,
        counts: null,
      },
    ]);
  });
});

describe('mintedAtFor', () => {
  const rows = runListRows([DELIVERED_RUN, ESCALATED_RUN]);

  it('answers the minted instant off the matching row', () => {
    expect(mintedAtFor(rows, 'ch_2')).toBe('2026-01-11T00:00:00Z');
  });

  it('answers null for a chunk no row carries', () => {
    expect(mintedAtFor(rows, 'ch_gone')).toBeNull();
  });
});
